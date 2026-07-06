"""MEx 多源时空对齐与融合 Web 演示。

直接从 mex.zip/data.zip 流式读取四类传感器数据，不需要解压数据集。
运行：python web_app.py
"""
from __future__ import annotations

import csv
import io
import json
import math
import re
import statistics
import zipfile
from bisect import bisect_left
from datetime import datetime
from functools import lru_cache
from pathlib import Path

from flask import Flask, jsonify, request, send_from_directory


ROOT = Path(__file__).resolve().parent
MEX_ZIP = ROOT / "mex.zip"
SAMPLE_RE = re.compile(r"([^/]+)/([0-9]+)/([0-9]+)_([^_]+)_([0-9]+)\.csv$")
MODALITIES = ("act", "acw", "dc_0.05_0.05", "pm_1.0_1.0")
MODALITY_NAMES = {
    "act": "振动数据",
    "acw": "音频数据",
    "dc_0.05_0.05": "视频数据",
    "pm_1.0_1.0": "温度图片",
}
ACTIVITY_NAMES = {
    "01": "原地站立", "02": "坐下起立", "03": "抬臂运动", "04": "步行运动",
    "05": "弯腰运动", "06": "原地跳跃", "07": "侧向运动",
}

app = Flask(__name__, static_folder="static")


@lru_cache(maxsize=1)
def inner_zip_bytes() -> bytes:
    if not MEX_ZIP.exists():
        raise FileNotFoundError(f"未找到数据集：{MEX_ZIP}")
    with zipfile.ZipFile(MEX_ZIP) as outer:
        return outer.read("data.zip")


def open_inner() -> zipfile.ZipFile:
    return zipfile.ZipFile(io.BytesIO(inner_zip_bytes()))


@lru_cache(maxsize=1)
def dataset_index() -> dict:
    groups: dict[str, set[tuple[str, str]]] = {}
    available: set[tuple[str, str, str]] = set()
    with open_inner() as zf:
        for name in zf.namelist():
            match = SAMPLE_RE.match(name)
            if not match:
                continue
            modality, subject, activity, _, trial = match.groups()
            available.add((modality, subject, activity + "_" + trial))
            if modality == "act":
                groups.setdefault(activity, set()).add((subject, trial))
    items = []
    for activity in sorted(groups):
        samples = sorted(groups[activity])
        complete = [s for s in samples if all((m, s[0], activity + "_" + s[1]) in available for m in MODALITIES)]
        items.append({
            "id": activity,
            "name": ACTIVITY_NAMES.get(activity, f"活动 {activity}"),
            "count": len(complete),
            "samples": [{"subject": s, "trial": t, "label": f"受试者 {s} · 第 {t} 次"} for s, t in complete],
        })
    return {"groups": items, "total": sum(x["count"] for x in items), "modalities": len(MODALITIES)}


def parse_time(value: str) -> float:
    return datetime.fromisoformat(value.strip()).timestamp()


def row_feature(values: list[str], modality: str) -> float:
    nums = []
    for value in values:
        try:
            nums.append(float(value))
        except (TypeError, ValueError):
            pass
    if not nums:
        return 0.0
    if modality in ("act", "acw"):
        return math.sqrt(sum(x * x for x in nums[:3]))
    # 高维深度/压力数据用 RMS 能量压缩为可比较的一维特征。
    return math.sqrt(sum(x * x for x in nums) / len(nums))


def sample_path(modality: str, subject: str, activity: str, trial: str) -> str:
    short = {"act": "act", "acw": "acw", "dc_0.05_0.05": "dc", "pm_1.0_1.0": "pm"}[modality]
    return f"{modality}/{subject}/{activity}_{short}_{trial}.csv"


@lru_cache(maxsize=32)
def load_series(modality: str, subject: str, activity: str, trial: str) -> tuple[tuple[float, float], ...]:
    path = sample_path(modality, subject, activity, trial)
    with open_inner() as zf:
        text = io.TextIOWrapper(zf.open(path), encoding="utf-8-sig", newline="")
        rows = []
        for row in csv.reader(text):
            if len(row) < 2:
                continue
            try:
                rows.append((parse_time(row[0]), row_feature(row[1:], modality)))
            except ValueError:
                continue
    # 浏览器图表与计算最多保留 240 个均匀采样点。
    if len(rows) > 240:
        step = (len(rows) - 1) / 239
        rows = [rows[round(i * step)] for i in range(240)]
    return tuple(rows)


def normalize(values: list[float]) -> list[float]:
    if not values:
        return []
    lo, hi = min(values), max(values)
    if hi - lo < 1e-12:
        return [0.5] * len(values)
    return [(x - lo) / (hi - lo) for x in values]


def nearest_index(times: list[float], target: float) -> int:
    pos = bisect_left(times, target)
    if pos <= 0:
        return 0
    if pos >= len(times):
        return len(times) - 1
    return pos if abs(times[pos] - target) < abs(times[pos - 1] - target) else pos - 1


def pearson(a: list[float], b: list[float]) -> float:
    if len(a) < 2 or len(a) != len(b):
        return 0.0
    ma, mb = statistics.fmean(a), statistics.fmean(b)
    da, db = [x - ma for x in a], [x - mb for x in b]
    den = math.sqrt(sum(x*x for x in da) * sum(x*x for x in db))
    return sum(x*y for x, y in zip(da, db)) / den if den else 0.0


def cosine(a: list[float], b: list[float]) -> float:
    den = math.sqrt(sum(x*x for x in a) * sum(x*x for x in b))
    return sum(x*y for x, y in zip(a, b)) / den if den else 0.0


def series_payload(series: dict[str, list[tuple[float, float]]]) -> list[dict]:
    base = min(points[0][0] for points in series.values() if points)
    colors = {"act": "#6C7BFF", "acw": "#26C6A2", "dc_0.05_0.05": "#F5A45D", "pm_1.0_1.0": "#DA72D6"}
    payload = []
    for key in MODALITIES:
        points = series[key]
        vals = normalize([p[1] for p in points])
        payload.append({"key": key, "name": MODALITY_NAMES[key], "color": colors[key],
                        "points": [[round(p[0] - base, 3), round(v, 5)] for p, v in zip(points, vals)]})
    return payload


def align_sample(subject: str, activity: str, trial: str) -> dict:
    raw = {m: list(load_series(m, subject, activity, trial)) for m in MODALITIES}
    ref = raw["act"]
    ref_times = [p[0] for p in ref]
    aligned: dict[str, list[tuple[float, float]]] = {"act": ref}
    before_errors, after_errors = [], []
    aligned_values = {"act": normalize([p[1] for p in ref])}

    for modality in MODALITIES[1:]:
        points = raw[modality]
        times = [p[0] for p in points]
        matched = []
        for i, target in enumerate(ref_times):
            idx = nearest_index(times, target)
            matched.append((target, points[idx][1]))
            after_errors.append(abs(times[idx] - target) * 1000)
            proportional = min(round(i * (len(times)-1) / max(len(ref_times)-1, 1)), len(times)-1)
            before_errors.append(abs(times[proportional] - target) * 1000)
        aligned[modality] = matched
        aligned_values[modality] = normalize([x[1] for x in matched])

    correlations = [pearson(aligned_values["act"], aligned_values[m]) for m in MODALITIES[1:]]
    cosines = [cosine(aligned_values["act"], aligned_values[m]) for m in MODALITIES[1:]]
    rmse = lambda xs: math.sqrt(statistics.fmean([x*x for x in xs])) if xs else 0.0
    metrics = [
        ("均方根误差 RMSE", rmse(after_errors), "ms", "越低越好"),
        ("Hausdorff 距离", max(after_errors, default=0), "ms", "越低越好"),
        ("Chamfer 距离", statistics.fmean(after_errors) if after_errors else 0, "ms", "越低越好"),
        ("时间延迟偏差", statistics.median(after_errors) if after_errors else 0, "ms", "越低越好"),
        ("归一化相关", statistics.fmean(correlations), "", "越高越好"),
        ("中心核对齐", statistics.fmean(cosines), "", "越高越好"),
    ]
    time_axis = [round(timestamp - ref_times[0], 3) for timestamp in ref_times]
    return {
        "raw": series_payload(raw), "aligned": series_payload(aligned),
        "aligned_values": aligned_values, "timeAxis": time_axis,
        "metrics": [{"name": n, "value": round(v, 4), "unit": u, "trend": t} for n,v,u,t in metrics],
        "summary": {
            "points": len(ref), "beforeRmse": round(rmse(before_errors), 3),
            "afterRmse": round(rmse(after_errors), 3),
            "duration": round(time_axis[-1], 3) if time_axis else 0,
            "successRate": round(100 * sum(x <= 50 for x in after_errors) / max(len(after_errors), 1), 1),
        },
    }


def entropy(values: list[float], bins: int = 12) -> float:
    counts = [0] * bins
    for value in values:
        counts[min(bins - 1, max(0, int(value * bins)))] += 1
    total = max(len(values), 1)
    return -sum((c/total) * math.log2(c/total) for c in counts if c)


def mutual_information(a: list[float], b: list[float], bins: int = 8) -> float:
    joint = [[0] * bins for _ in range(bins)]
    for x, y in zip(a, b):
        joint[min(bins-1, int(x*bins))][min(bins-1, int(y*bins))] += 1
    n = max(len(a), 1); px = [sum(r)/n for r in joint]; py = [sum(joint[i][j] for i in range(bins))/n for j in range(bins)]
    return sum((joint[i][j]/n) * math.log2((joint[i][j]/n)/(px[i]*py[j]))
               for i in range(bins) for j in range(bins) if joint[i][j] and px[i] and py[j])


def fuse_sample(alignment: dict, weights: list[float]) -> dict:
    values = alignment["aligned_values"]
    total = sum(weights) or 1.0
    weights = [w / total for w in weights]
    fused = [sum(weights[j] * values[m][i] for j, m in enumerate(MODALITIES)) for i in range(len(values["act"]))]
    consensus = [statistics.fmean(values[m][i] for m in MODALITIES) for i in range(len(fused))]
    # 以融合特征相对所有源模态的平均重建误差计算 PSNR，避免等权融合
    # 恰好等于简单均值时出现 MSE=0、PSNR 固定触顶的问题。
    reconstruction_errors = [
        (fused[i] - values[modality][i]) ** 2
        for modality in MODALITIES for i in range(len(fused))
    ]
    mse = statistics.fmean(reconstruction_errors) if reconstruction_errors else 0
    gradients = [abs(fused[i]-fused[i-1]) for i in range(1, len(fused))]
    source_grad = [abs(consensus[i]-consensus[i-1]) for i in range(1, len(consensus))]
    avg_corr = statistics.fmean(max(0, pearson(fused, values[m])) for m in MODALITIES)
    avg_mi = statistics.fmean(mutual_information(fused, values[m]) for m in MODALITIES)
    mmd = statistics.fmean((statistics.fmean(fused)-statistics.fmean(values[m]))**2 for m in MODALITIES)
    frob = math.sqrt(sum(sum(x*x for x in values[m]) for m in MODALITIES))
    variances = [statistics.pvariance(values[m]) for m in MODALITIES]
    # MEx 不含异常标签：该分数只表示归一化运动/空间响应强度，用于展示
    # 原融合代码中的“得分—阈值—判别”完整输出流程。
    modality_scores = []
    for modality in MODALITIES:
        ordered = sorted(values[modality])
        p90 = ordered[min(len(ordered) - 1, round((len(ordered) - 1) * 0.90))]
        modality_scores.append(0.65 * statistics.fmean(values[modality]) + 0.35 * p90)
    contribution_values = [weights[i] * modality_scores[i] for i in range(4)]
    decision_score = sum(contribution_values)
    if decision_score >= 0.70:
        decision_label, decision_level = "异常", "danger"
    elif decision_score >= 0.50:
        decision_label, decision_level = "疑似异常", "warning"
    else:
        decision_label, decision_level = "正常", "normal"
    ranked = sorted(
        zip(MODALITIES, contribution_values),
        key=lambda item: item[1], reverse=True,
    )
    main_sources = [MODALITY_NAMES[modality] for modality, _ in ranked[:2]]
    alignment_ok = alignment["summary"]["successRate"] >= 75
    metrics = [
        ("互信息 MI", avg_mi, "bit", "越大表示共享信息越多"), ("融合熵", entropy(fused), "bit", "越大表示信息越丰富"),
        ("结构相似性", avg_corr, "", "越大表示结构越一致"),
        ("峰值信噪比 PSNR", min(60.0, 10*math.log10(1/max(mse, 1e-12))), "dB", "越大越好（展示上限 60 dB）"),
        ("梯度保真度", max(0, pearson(gradients, source_grad)), "", "越大表示边缘保留越好"),
        ("空间频率", math.sqrt(statistics.fmean(g*g for g in gradients)) if gradients else 0, "", "描述细节变化活跃度"),
        ("平均梯度", statistics.fmean(gradients) if gradients else 0, "", "越大通常表示细节越清晰"),
        ("MMD", mmd, "", "越小表示分布差异越小"),
        ("Frobenius 范数", frob, "", "描述融合特征整体能量"),
        ("稳定系数", max(variances)/max(min(variances), 1e-9), "", "越低越稳定"),
    ]
    contribution_total = sum(contribution_values) or 1.0
    contribution = [
        {
            "name": MODALITY_NAMES[modality],
            "weight": round(weights[i] * 100, 1),
            "anomalyScore": round(modality_scores[i], 4),
            "contribution": round(contribution_values[i], 4),
            "value": round(contribution_values[i] / contribution_total * 100, 1),
        }
        for i, modality in enumerate(MODALITIES)
    ]
    psnr_display = min(60.0, 10*math.log10(1/max(mse, 1e-12)))
    if decision_label == "疑似异常":
        reason = "当前融合得分处于疑似异常区间，说明部分模态存在异常波动，但整体异常强度尚未达到异常阈值。"
    elif decision_label == "异常":
        reason = "当前融合得分达到异常区间，多个模态的加权异常响应较强。"
    else:
        reason = "当前融合得分低于疑似异常阈值，各模态的整体加权响应处于正常区间。"
    return {
        "fused": [round(x, 5) for x in fused],
        "fusedSeries": [[alignment["timeAxis"][i], round(value, 5)] for i, value in enumerate(fused)],
        "contribution": contribution,
        "metrics": [{"name": n, "value": round(v, 4), "unit": u, "trend": t} for n,v,u,t in metrics],
        "summary": {"information": round(min(100, avg_mi/3*100), 1), "structure": round(avg_corr*100, 1),
                    "psnr": round(psnr_display, 2), "points": len(fused),
                    "beforeRmse": alignment["summary"]["beforeRmse"],
                    "afterRmse": alignment["summary"]["afterRmse"],
                    "successRate": alignment["summary"]["successRate"],
                    "duration": alignment["summary"]["duration"]},
        "decision": {
            "label": decision_label, "level": decision_level,
            "score": round(decision_score, 4), "confidence": round(decision_score * 100, 1),
            "mainSources": main_sources,
            "alignment": "对齐成功" if alignment_ok else "部分对齐",
            "alignmentRate": alignment["summary"]["successRate"],
            "rule": "融合得分 < 0.50：正常；0.50 ≤ 融合得分 < 0.70：疑似异常；融合得分 ≥ 0.70：异常。",
            "reason": reason,
            "note": "主要贡献模态由各模态异常得分与融合权重共同决定，并非仅由权重决定。",
        },
    }


def selection() -> tuple[str, str, str]:
    data = request.get_json(silent=True) or {}
    subject, activity, trial = str(data.get("subject", "01")), str(data.get("activity", "01")), str(data.get("trial", "1"))
    return subject.zfill(2), activity.zfill(2), trial


@app.get("/")
def index_page():
    return send_from_directory(ROOT / "static", "index.html")


@app.get("/api/dataset")
def dataset_api():
    return jsonify(dataset_index())


@app.post("/api/preview")
def preview_api():
    subject, activity, trial = selection()
    raw = {m: list(load_series(m, subject, activity, trial)) for m in MODALITIES}
    return jsonify({"series": series_payload(raw), "rows": sum(len(x) for x in raw.values()),
                    "duration": round(max(x[-1][0] for x in raw.values()) - min(x[0][0] for x in raw.values()), 2)})


@app.post("/api/align")
def align_api():
    subject, activity, trial = selection()
    return jsonify(align_sample(subject, activity, trial))


@app.post("/api/fuse")
def fuse_api():
    subject, activity, trial = selection()
    data = request.get_json(silent=True) or {}
    weights = data.get("weights", [0.25, 0.25, 0.25, 0.25])
    if not isinstance(weights, list) or len(weights) != 4:
        weights = [0.25] * 4
    alignment = align_sample(subject, activity, trial)
    return jsonify(fuse_sample(alignment, [max(0, float(x)) for x in weights]))


@app.get("/api/export")
def export_api():
    subject = request.args.get("subject", "01").zfill(2); activity = request.args.get("activity", "01").zfill(2); trial = request.args.get("trial", "1")
    result = {"sample": {"subject": subject, "activity": activity, "trial": trial}, "alignment": align_sample(subject, activity, trial)}
    result["fusion"] = fuse_sample(result["alignment"], [0.25] * 4)
    return app.response_class(json.dumps(result, ensure_ascii=False, indent=2), mimetype="application/json",
                              headers={"Content-Disposition": f"attachment; filename=mex_result_{subject}_{activity}_{trial}.json"})


if __name__ == "__main__":
    print("MEx 对齐融合可视化：http://127.0.0.1:5000")
    app.run(host="127.0.0.1", port=5000, debug=False)
