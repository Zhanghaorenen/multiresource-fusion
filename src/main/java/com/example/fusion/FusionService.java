package com.example.fusion;

import java.io.BufferedReader;
import java.io.ByteArrayInputStream;
import java.io.IOException;
import java.io.InputStream;
import java.io.InputStreamReader;
import java.nio.charset.StandardCharsets;
import java.nio.file.Files;
import java.nio.file.Path;
import java.time.OffsetDateTime;
import java.time.ZoneId;
import java.time.format.DateTimeFormatter;
import java.time.format.DateTimeFormatterBuilder;
import java.time.temporal.ChronoField;
import java.util.ArrayList;
import java.util.Arrays;
import java.util.Comparator;
import java.util.HashMap;
import java.util.HashSet;
import java.util.LinkedHashMap;
import java.util.List;
import java.util.Locale;
import java.util.Map;
import java.util.Objects;
import java.util.Set;
import java.util.TreeMap;
import java.util.regex.Matcher;
import java.util.regex.Pattern;
import java.util.stream.Collectors;
import java.util.zip.ZipEntry;
import java.util.zip.ZipFile;
import java.util.zip.ZipInputStream;

import org.springframework.stereotype.Service;
import org.springframework.util.StreamUtils;

@Service
public class FusionService {
    private static final Path ROOT = Path.of("").toAbsolutePath();
    private static final Path MEX_ZIP = ROOT.resolve("mex.zip");
    private static final Pattern SAMPLE_RE = Pattern.compile("([^/]+)/([0-9]+)/([0-9]+)_([^_]+)_([0-9]+)\\.csv$");
    private static final List<String> MODALITIES = List.of("act", "acw", "dc_0.05_0.05", "pm_1.0_1.0");
    private static final Map<String, String> MODALITY_NAMES = Map.of(
            "act", "振动数据",
            "acw", "音频数据",
            "dc_0.05_0.05", "视频数据",
            "pm_1.0_1.0", "温度图片");
    private static final Map<String, String> ACTIVITY_NAMES = Map.of(
            "01", "原地站立",
            "02", "坐下起立",
            "03", "抬臂运动",
            "04", "步行运动",
            "05", "弯腰运动",
            "06", "原地跳跃",
            "07", "侧向运动");
    private static final Map<String, String> COLORS = Map.of(
            "act", "#6C7BFF",
            "acw", "#26C6A2",
            "dc_0.05_0.05", "#F5A45D",
            "pm_1.0_1.0", "#DA72D6");

    private byte[] innerZipBytes;
    private Map<String, Object> datasetIndex;
    private final Map<String, List<Point>> seriesCache = new HashMap<>();
    private final Map<String, Map<String, Object>> alignmentCache = new HashMap<>();

    public synchronized Map<String, Object> datasetIndex() {
        if (datasetIndex != null) {
            return datasetIndex;
        }
        Map<String, Set<SampleKey>> groups = new TreeMap<>();
        Set<String> available = new HashSet<>();
        for (String name : innerNames()) {
            Matcher matcher = SAMPLE_RE.matcher(name);
            if (!matcher.matches()) {
                continue;
            }
            String modality = matcher.group(1);
            String subject = matcher.group(2);
            String activity = matcher.group(3);
            String trial = matcher.group(5);
            available.add(modality + "|" + subject + "|" + activity + "_" + trial);
            if ("act".equals(modality)) {
                groups.computeIfAbsent(activity, key -> new HashSet<>()).add(new SampleKey(subject, trial));
            }
        }
        List<Map<String, Object>> items = new ArrayList<>();
        for (Map.Entry<String, Set<SampleKey>> entry : groups.entrySet()) {
            String activity = entry.getKey();
            List<SampleKey> complete = entry.getValue().stream()
                    .filter(sample -> MODALITIES.stream()
                            .allMatch(modality -> available.contains(modality + "|" + sample.subject() + "|" + activity + "_" + sample.trial())))
                    .sorted(Comparator.comparing(SampleKey::subject).thenComparing(sample -> Integer.parseInt(sample.trial())))
                    .toList();
            List<Map<String, Object>> samples = complete.stream()
                    .map(sample -> mapOf(
                            "subject", sample.subject(),
                            "trial", sample.trial(),
                            "label", "受试者 %s · 第 %s 次".formatted(sample.subject(), sample.trial())))
                    .toList();
            items.add(mapOf(
                    "id", activity,
                    "name", ACTIVITY_NAMES.getOrDefault(activity, "活动 " + activity),
                    "count", complete.size(),
                    "samples", samples));
        }
        datasetIndex = mapOf("groups", items, "total", items.stream().mapToInt(item -> ((Number) item.get("count")).intValue()).sum(), "modalities", MODALITIES.size());
        return datasetIndex;
    }

    public Map<String, Object> preview(String subject, String activity, String trial) {
        Map<String, List<Point>> raw = loadAll(subject, activity, trial);
        double min = raw.values().stream().filter(points -> !points.isEmpty()).mapToDouble(points -> points.get(0).time()).min().orElse(0);
        double max = raw.values().stream().filter(points -> !points.isEmpty()).mapToDouble(points -> points.get(points.size() - 1).time()).max().orElse(0);
        int rows = raw.values().stream().mapToInt(List::size).sum();
        return mapOf("series", seriesPayload(raw), "rows", rows, "duration", round(max - min, 2));
    }

    public synchronized Map<String, Object> alignSample(String subject, String activity, String trial) {
        String cacheKey = subject + "|" + activity + "|" + trial;
        if (alignmentCache.containsKey(cacheKey)) {
            return alignmentCache.get(cacheKey);
        }
        Map<String, List<Point>> raw = loadAll(subject, activity, trial);
        List<Point> ref = raw.get("act");
        List<Double> refTimes = ref.stream().map(Point::time).toList();
        Map<String, List<Point>> aligned = new LinkedHashMap<>();
        aligned.put("act", ref);
        List<Double> beforeErrors = new ArrayList<>();
        List<Double> afterErrors = new ArrayList<>();
        Map<String, List<Double>> alignedValues = new LinkedHashMap<>();
        alignedValues.put("act", normalize(ref.stream().map(Point::value).toList()));

        for (String modality : MODALITIES.subList(1, MODALITIES.size())) {
            List<Point> points = raw.get(modality);
            List<Double> times = points.stream().map(Point::time).toList();
            List<Point> matched = new ArrayList<>();
            for (int i = 0; i < refTimes.size(); i++) {
                double target = refTimes.get(i);
                int index = nearestIndex(times, target);
                matched.add(new Point(target, points.get(index).value()));
                afterErrors.add(Math.abs(times.get(index) - target) * 1000);
                int proportional = Math.min((int) Math.round(i * (times.size() - 1.0) / Math.max(refTimes.size() - 1.0, 1.0)), times.size() - 1);
                beforeErrors.add(Math.abs(times.get(proportional) - target) * 1000);
            }
            aligned.put(modality, matched);
            alignedValues.put(modality, normalize(matched.stream().map(Point::value).toList()));
        }

        List<Double> correlations = MODALITIES.subList(1, MODALITIES.size()).stream()
                .map(modality -> pearson(alignedValues.get("act"), alignedValues.get(modality))).toList();
        List<Double> cosines = MODALITIES.subList(1, MODALITIES.size()).stream()
                .map(modality -> cosine(alignedValues.get("act"), alignedValues.get(modality))).toList();
        List<Map<String, Object>> metrics = List.of(
                metric("均方根误差 RMSE", rmse(afterErrors), "ms", "越低越好"),
                metric("Hausdorff 距离", afterErrors.stream().mapToDouble(Double::doubleValue).max().orElse(0), "ms", "越低越好"),
                metric("Chamfer 距离", mean(afterErrors), "ms", "越低越好"),
                metric("时间延迟偏差", median(afterErrors), "ms", "越低越好"),
                metric("归一化相关", mean(correlations), "", "越高越好"),
                metric("中心核对齐", mean(cosines), "", "越高越好"));
        double base = refTimes.isEmpty() ? 0 : refTimes.get(0);
        List<Double> timeAxis = refTimes.stream().map(timestamp -> round(timestamp - base, 3)).toList();
        Map<String, Object> result = mapOf(
                "raw", seriesPayload(raw),
                "aligned", seriesPayload(aligned),
                "aligned_values", alignedValues,
                "timeAxis", timeAxis,
                "metrics", metrics,
                "summary", mapOf(
                        "points", ref.size(),
                        "beforeRmse", round(rmse(beforeErrors), 3),
                        "afterRmse", round(rmse(afterErrors), 3),
                        "duration", timeAxis.isEmpty() ? 0 : round(timeAxis.get(timeAxis.size() - 1), 3),
                        "successRate", round(100.0 * afterErrors.stream().filter(value -> value <= 50).count() / Math.max(afterErrors.size(), 1), 1)));
        alignmentCache.put(cacheKey, result);
        return result;
    }

    @SuppressWarnings("unchecked")
    public Map<String, Object> fuseSample(Map<String, Object> alignment, List<Double> inputWeights) {
        Map<String, List<Double>> values = (Map<String, List<Double>>) alignment.get("aligned_values");
        List<Double> weights = normalizedWeights(inputWeights);
        List<Double> act = values.get("act");
        List<Double> fused = new ArrayList<>();
        List<Double> consensus = new ArrayList<>();
        for (int i = 0; i < act.size(); i++) {
            double fusedValue = 0;
            double sum = 0;
            for (int j = 0; j < MODALITIES.size(); j++) {
                double value = values.get(MODALITIES.get(j)).get(i);
                fusedValue += weights.get(j) * value;
                sum += value;
            }
            fused.add(fusedValue);
            consensus.add(sum / MODALITIES.size());
        }
        List<Double> reconstructionErrors = new ArrayList<>();
        for (String modality : MODALITIES) {
            for (int i = 0; i < fused.size(); i++) {
                reconstructionErrors.add(Math.pow(fused.get(i) - values.get(modality).get(i), 2));
            }
        }
        double mse = mean(reconstructionErrors);
        List<Double> gradients = diffs(fused);
        List<Double> sourceGrad = diffs(consensus);
        double avgCorr = mean(MODALITIES.stream().map(modality -> Math.max(0, pearson(fused, values.get(modality)))).toList());
        double avgMi = mean(MODALITIES.stream().map(modality -> mutualInformation(fused, values.get(modality), 8)).toList());
        double fusedMean = mean(fused);
        double mmd = mean(MODALITIES.stream().map(modality -> Math.pow(fusedMean - mean(values.get(modality)), 2)).toList());
        double frob = Math.sqrt(MODALITIES.stream().mapToDouble(modality -> values.get(modality).stream().mapToDouble(value -> value * value).sum()).sum());
        List<Double> variances = MODALITIES.stream().map(modality -> variance(values.get(modality))).toList();

        List<Double> modalityScores = new ArrayList<>();
        for (String modality : MODALITIES) {
            List<Double> ordered = new ArrayList<>(values.get(modality));
            ordered.sort(Double::compareTo);
            double p90 = ordered.get(Math.min(ordered.size() - 1, (int) Math.round((ordered.size() - 1) * 0.90)));
            modalityScores.add(0.65 * mean(values.get(modality)) + 0.35 * p90);
        }
        List<Double> contributionValues = new ArrayList<>();
        for (int i = 0; i < MODALITIES.size(); i++) {
            contributionValues.add(weights.get(i) * modalityScores.get(i));
        }
        double decisionScore = contributionValues.stream().mapToDouble(Double::doubleValue).sum();
        String decisionLabel;
        String decisionLevel;
        if (decisionScore >= 0.70) {
            decisionLabel = "异常";
            decisionLevel = "danger";
        } else if (decisionScore >= 0.50) {
            decisionLabel = "疑似异常";
            decisionLevel = "warning";
        } else {
            decisionLabel = "正常";
            decisionLevel = "normal";
        }
        List<String> mainSources = contributionValues.stream()
                .map(value -> new IndexedValue(contributionValues.indexOf(value), value))
                .sorted(Comparator.comparing(IndexedValue::value).reversed())
                .limit(2)
                .map(item -> MODALITY_NAMES.get(MODALITIES.get(item.index())))
                .toList();
        Map<String, Object> summary = castMap(alignment.get("summary"));
        boolean alignmentOk = ((Number) summary.get("successRate")).doubleValue() >= 75;
        double psnrDisplay = Math.min(60.0, 10 * Math.log10(1 / Math.max(mse, 1e-12)));
        List<Map<String, Object>> metrics = List.of(
                metric("互信息 MI", avgMi, "bit", "越大表示共享信息越多"),
                metric("融合熵", entropy(fused, 12), "bit", "越大表示信息越丰富"),
                metric("结构相似性", avgCorr, "", "越大表示结构越一致"),
                metric("峰值信噪比 PSNR", psnrDisplay, "dB", "越大越好（展示上限 60 dB）"),
                metric("梯度保真度", Math.max(0, pearson(gradients, sourceGrad)), "", "越大表示边缘保留越好"),
                metric("空间频率", gradients.isEmpty() ? 0 : Math.sqrt(mean(gradients.stream().map(value -> value * value).toList())), "", "描述细节变化活跃度"),
                metric("平均梯度", mean(gradients), "", "越大通常表示细节越清晰"),
                metric("MMD", mmd, "", "越小表示分布差异越小"),
                metric("Frobenius 范数", frob, "", "描述融合特征整体能量"),
                metric("稳定系数", variances.stream().mapToDouble(Double::doubleValue).max().orElse(0) / Math.max(variances.stream().mapToDouble(Double::doubleValue).min().orElse(0), 1e-9), "", "越低越稳定"));
        double contributionTotal = contributionValues.stream().mapToDouble(Double::doubleValue).sum();
        if (contributionTotal == 0) {
            contributionTotal = 1;
        }
        List<Map<String, Object>> contribution = new ArrayList<>();
        for (int i = 0; i < MODALITIES.size(); i++) {
            String modality = MODALITIES.get(i);
            contribution.add(mapOf(
                    "name", MODALITY_NAMES.get(modality),
                    "weight", round(weights.get(i) * 100, 1),
                    "anomalyScore", round(modalityScores.get(i), 4),
                    "contribution", round(contributionValues.get(i), 4),
                    "value", round(contributionValues.get(i) / contributionTotal * 100, 1)));
        }
        String reason = switch (decisionLabel) {
            case "疑似异常" -> "当前融合得分处于疑似异常区间，说明部分模态存在异常波动，但整体异常强度尚未达到异常阈值。";
            case "异常" -> "当前融合得分达到异常区间，多个模态的加权异常响应较强。";
            default -> "当前融合得分低于疑似异常阈值，各模态的整体加权响应处于正常区间。";
        };
        List<Double> timeAxis = (List<Double>) alignment.get("timeAxis");
        List<List<Double>> fusedSeries = new ArrayList<>();
        for (int i = 0; i < fused.size(); i++) {
            fusedSeries.add(List.of(timeAxis.get(i), round(fused.get(i), 5)));
        }
        return mapOf(
                "fused", fused.stream().map(value -> round(value, 5)).toList(),
                "fusedSeries", fusedSeries,
                "contribution", contribution,
                "metrics", metrics,
                "summary", mapOf(
                        "information", round(Math.min(100, avgMi / 3 * 100), 1),
                        "structure", round(avgCorr * 100, 1),
                        "psnr", round(psnrDisplay, 2),
                        "points", fused.size(),
                        "beforeRmse", summary.get("beforeRmse"),
                        "afterRmse", summary.get("afterRmse"),
                        "successRate", summary.get("successRate"),
                        "duration", summary.get("duration")),
                "decision", mapOf(
                        "label", decisionLabel,
                        "level", decisionLevel,
                        "score", round(decisionScore, 4),
                        "confidence", round(decisionScore * 100, 1),
                        "mainSources", mainSources,
                        "alignment", alignmentOk ? "对齐成功" : "部分对齐",
                        "alignmentRate", summary.get("successRate"),
                        "rule", "融合得分 < 0.50：正常；0.50 ≤ 融合得分 < 0.70：疑似异常；融合得分 ≥ 0.70：异常。",
                        "reason", reason,
                        "note", "主要贡献模态由各模态异常得分与融合权重共同决定，并非仅由权重决定。"));
    }

    public static List<Double> weightsFrom(Object value) {
        if (!(value instanceof List<?> raw) || raw.size() != 4) {
            return List.of(0.25, 0.25, 0.25, 0.25);
        }
        return raw.stream().map(item -> {
            if (item instanceof Number number) {
                return Math.max(0, number.doubleValue());
            }
            try {
                return Math.max(0, Double.parseDouble(String.valueOf(item)));
            } catch (NumberFormatException ex) {
                return 0.25;
            }
        }).toList();
    }

    private Map<String, List<Point>> loadAll(String subject, String activity, String trial) {
        Map<String, List<Point>> raw = new LinkedHashMap<>();
        for (String modality : MODALITIES) {
            raw.put(modality, loadSeries(modality, subject, activity, trial));
        }
        return raw;
    }

    private synchronized List<Point> loadSeries(String modality, String subject, String activity, String trial) {
        String key = String.join("|", modality, subject, activity, trial);
        if (seriesCache.containsKey(key)) {
            return seriesCache.get(key);
        }
        String path = samplePath(modality, subject, activity, trial);
        List<Point> rows = new ArrayList<>();
        try (ZipInputStream zis = new ZipInputStream(new ByteArrayInputStream(innerZipBytes()))) {
            ZipEntry entry;
            while ((entry = zis.getNextEntry()) != null) {
                if (!path.equals(entry.getName())) {
                    continue;
                }
                BufferedReader reader = new BufferedReader(new InputStreamReader(zis, StandardCharsets.UTF_8));
                String line;
                while ((line = reader.readLine()) != null) {
                    List<String> parts = parseCsvLine(stripBom(line));
                    if (parts.size() < 2) {
                        continue;
                    }
                    try {
                        rows.add(new Point(parseTime(parts.get(0)), rowFeature(parts.subList(1, parts.size()), modality)));
                    } catch (RuntimeException ignored) {
                        // Skip headers or malformed rows, matching the permissive Flask implementation.
                    }
                }
                break;
            }
        } catch (IOException ex) {
            throw new IllegalStateException("读取数据失败：" + path, ex);
        }
        if (rows.size() > 240) {
            List<Point> sampled = new ArrayList<>();
            double step = (rows.size() - 1.0) / 239;
            for (int i = 0; i < 240; i++) {
                sampled.add(rows.get((int) Math.round(i * step)));
            }
            rows = sampled;
        }
        if (rows.isEmpty()) {
            throw new IllegalArgumentException("未找到样本数据：" + path);
        }
        List<Point> immutable = List.copyOf(rows);
        seriesCache.put(key, immutable);
        return immutable;
    }

    private List<Map<String, Object>> seriesPayload(Map<String, List<Point>> series) {
        double base = series.values().stream().filter(points -> !points.isEmpty()).mapToDouble(points -> points.get(0).time()).min().orElse(0);
        List<Map<String, Object>> payload = new ArrayList<>();
        for (String key : MODALITIES) {
            List<Point> points = series.get(key);
            List<Double> vals = normalize(points.stream().map(Point::value).toList());
            List<List<Double>> plotted = new ArrayList<>();
            for (int i = 0; i < points.size(); i++) {
                plotted.add(List.of(round(points.get(i).time() - base, 3), round(vals.get(i), 5)));
            }
            payload.add(mapOf("key", key, "name", MODALITY_NAMES.get(key), "color", COLORS.get(key), "points", plotted));
        }
        return payload;
    }

    private byte[] innerZipBytes() {
        if (innerZipBytes != null) {
            return innerZipBytes;
        }
        if (!Files.exists(MEX_ZIP)) {
            throw new IllegalStateException("未找到数据集：" + MEX_ZIP);
        }
        try (ZipFile outer = new ZipFile(MEX_ZIP.toFile())) {
            ZipEntry entry = outer.getEntry("data.zip");
            if (entry == null) {
                throw new IllegalStateException("mex.zip 中未找到 data.zip");
            }
            try (InputStream input = outer.getInputStream(entry)) {
                innerZipBytes = StreamUtils.copyToByteArray(input);
                return innerZipBytes;
            }
        } catch (IOException ex) {
            throw new IllegalStateException("读取 mex.zip 失败", ex);
        }
    }

    private List<String> innerNames() {
        List<String> names = new ArrayList<>();
        try (ZipInputStream zis = new ZipInputStream(new ByteArrayInputStream(innerZipBytes()))) {
            ZipEntry entry;
            while ((entry = zis.getNextEntry()) != null) {
                names.add(entry.getName());
            }
            return names;
        } catch (IOException ex) {
            throw new IllegalStateException("读取 data.zip 目录失败", ex);
        }
    }

    private static String samplePath(String modality, String subject, String activity, String trial) {
        String shortName = switch (modality) {
            case "act" -> "act";
            case "acw" -> "acw";
            case "dc_0.05_0.05" -> "dc";
            case "pm_1.0_1.0" -> "pm";
            default -> throw new IllegalArgumentException("未知模态：" + modality);
        };
        return "%s/%s/%s_%s_%s.csv".formatted(modality, subject, activity, shortName, trial);
    }

    private static double parseTime(String value) {
        String clean = value.trim();
        if (clean.endsWith("Z") || clean.matches(".+[+-][0-9]{2}:[0-9]{2}$")) {
            return OffsetDateTime.parse(clean).toInstant().toEpochMilli() / 1000.0;
        }
        DateTimeFormatter formatter = clean.contains("T")
                ? DateTimeFormatter.ISO_LOCAL_DATE_TIME
                : new DateTimeFormatterBuilder()
                        .appendPattern("yyyy-MM-dd HH:mm:ss")
                        .optionalStart()
                        .appendFraction(ChronoField.NANO_OF_SECOND, 1, 9, true)
                        .optionalEnd()
                        .toFormatter(Locale.ROOT);
        return formatter.parse(clean, java.time.LocalDateTime::from)
                .atZone(ZoneId.systemDefault()).toInstant().toEpochMilli() / 1000.0;
    }

    private static double rowFeature(List<String> values, String modality) {
        List<Double> nums = values.stream().map(value -> {
            try {
                return Double.parseDouble(value.trim());
            } catch (NumberFormatException ex) {
                return null;
            }
        }).filter(Objects::nonNull).toList();
        if (nums.isEmpty()) {
            return 0;
        }
        if ("act".equals(modality) || "acw".equals(modality)) {
            return Math.sqrt(nums.stream().limit(3).mapToDouble(value -> value * value).sum());
        }
        return Math.sqrt(nums.stream().mapToDouble(value -> value * value).sum() / nums.size());
    }

    private static List<String> parseCsvLine(String line) {
        List<String> cells = new ArrayList<>();
        StringBuilder cell = new StringBuilder();
        boolean quoted = false;
        for (int i = 0; i < line.length(); i++) {
            char ch = line.charAt(i);
            if (ch == '"') {
                if (quoted && i + 1 < line.length() && line.charAt(i + 1) == '"') {
                    cell.append('"');
                    i++;
                } else {
                    quoted = !quoted;
                }
            } else if (ch == ',' && !quoted) {
                cells.add(cell.toString());
                cell.setLength(0);
            } else {
                cell.append(ch);
            }
        }
        cells.add(cell.toString());
        return cells;
    }

    private static String stripBom(String line) {
        return line.startsWith("\uFEFF") ? line.substring(1) : line;
    }

    private static List<Double> normalize(List<Double> values) {
        if (values.isEmpty()) {
            return List.of();
        }
        double lo = values.stream().mapToDouble(Double::doubleValue).min().orElse(0);
        double hi = values.stream().mapToDouble(Double::doubleValue).max().orElse(0);
        if (hi - lo < 1e-12) {
            return values.stream().map(value -> 0.5).toList();
        }
        return values.stream().map(value -> (value - lo) / (hi - lo)).toList();
    }

    private static int nearestIndex(List<Double> times, double target) {
        int pos = java.util.Collections.binarySearch(times, target);
        if (pos >= 0) {
            return pos;
        }
        pos = -pos - 1;
        if (pos <= 0) {
            return 0;
        }
        if (pos >= times.size()) {
            return times.size() - 1;
        }
        return Math.abs(times.get(pos) - target) < Math.abs(times.get(pos - 1) - target) ? pos : pos - 1;
    }

    private static double pearson(List<Double> a, List<Double> b) {
        if (a.size() < 2 || a.size() != b.size()) {
            return 0;
        }
        double ma = mean(a);
        double mb = mean(b);
        double num = 0;
        double da = 0;
        double db = 0;
        for (int i = 0; i < a.size(); i++) {
            double xa = a.get(i) - ma;
            double xb = b.get(i) - mb;
            num += xa * xb;
            da += xa * xa;
            db += xb * xb;
        }
        double den = Math.sqrt(da * db);
        return den == 0 ? 0 : num / den;
    }

    private static double cosine(List<Double> a, List<Double> b) {
        double num = 0;
        double da = 0;
        double db = 0;
        for (int i = 0; i < Math.min(a.size(), b.size()); i++) {
            num += a.get(i) * b.get(i);
            da += a.get(i) * a.get(i);
            db += b.get(i) * b.get(i);
        }
        double den = Math.sqrt(da * db);
        return den == 0 ? 0 : num / den;
    }

    private static double entropy(List<Double> values, int bins) {
        int[] counts = new int[bins];
        for (double value : values) {
            counts[Math.min(bins - 1, Math.max(0, (int) (value * bins)))]++;
        }
        int total = Math.max(values.size(), 1);
        double result = 0;
        for (int count : counts) {
            if (count > 0) {
                double p = count / (double) total;
                result -= p * (Math.log(p) / Math.log(2));
            }
        }
        return result;
    }

    private static double mutualInformation(List<Double> a, List<Double> b, int bins) {
        int[][] joint = new int[bins][bins];
        for (int i = 0; i < Math.min(a.size(), b.size()); i++) {
            joint[Math.min(bins - 1, (int) (a.get(i) * bins))][Math.min(bins - 1, (int) (b.get(i) * bins))]++;
        }
        int n = Math.max(Math.min(a.size(), b.size()), 1);
        double[] px = new double[bins];
        double[] py = new double[bins];
        for (int i = 0; i < bins; i++) {
            px[i] = Arrays.stream(joint[i]).sum() / (double) n;
            for (int j = 0; j < bins; j++) {
                py[j] += joint[i][j] / (double) n;
            }
        }
        double result = 0;
        for (int i = 0; i < bins; i++) {
            for (int j = 0; j < bins; j++) {
                if (joint[i][j] > 0 && px[i] > 0 && py[j] > 0) {
                    double p = joint[i][j] / (double) n;
                    result += p * (Math.log(p / (px[i] * py[j])) / Math.log(2));
                }
            }
        }
        return result;
    }

    private static double rmse(List<Double> values) {
        return values.isEmpty() ? 0 : Math.sqrt(mean(values.stream().map(value -> value * value).toList()));
    }

    private static double mean(List<Double> values) {
        return values == null || values.isEmpty() ? 0 : values.stream().mapToDouble(Double::doubleValue).average().orElse(0);
    }

    private static double median(List<Double> values) {
        if (values == null || values.isEmpty()) {
            return 0;
        }
        List<Double> sorted = values.stream().sorted().toList();
        int mid = sorted.size() / 2;
        return sorted.size() % 2 == 1 ? sorted.get(mid) : (sorted.get(mid - 1) + sorted.get(mid)) / 2;
    }

    private static double variance(List<Double> values) {
        double mean = mean(values);
        return mean(values.stream().map(value -> Math.pow(value - mean, 2)).toList());
    }

    private static List<Double> diffs(List<Double> values) {
        List<Double> result = new ArrayList<>();
        for (int i = 1; i < values.size(); i++) {
            result.add(Math.abs(values.get(i) - values.get(i - 1)));
        }
        return result;
    }

    private static List<Double> normalizedWeights(List<Double> weights) {
        double total = weights.stream().mapToDouble(Double::doubleValue).sum();
        if (total == 0) {
            total = 1;
        }
        double denominator = total;
        return weights.stream().map(weight -> weight / denominator).toList();
    }

    private static Map<String, Object> metric(String name, double value, String unit, String trend) {
        return mapOf("name", name, "value", round(value, 4), "unit", unit, "trend", trend);
    }

    @SuppressWarnings("unchecked")
    private static Map<String, Object> castMap(Object value) {
        return (Map<String, Object>) value;
    }

    private static double round(double value, int places) {
        double factor = Math.pow(10, places);
        return Math.round(value * factor) / factor;
    }

    private static Map<String, Object> mapOf(Object... pairs) {
        Map<String, Object> map = new LinkedHashMap<>();
        for (int i = 0; i < pairs.length; i += 2) {
            map.put(String.valueOf(pairs[i]), pairs[i + 1]);
        }
        return map;
    }

    private record Point(double time, double value) {
    }

    private record SampleKey(String subject, String trial) {
    }

    private record IndexedValue(int index, double value) {
    }
}
