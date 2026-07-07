package com.example.fusion;

import java.nio.charset.StandardCharsets;
import java.util.List;
import java.util.Map;

import org.springframework.http.ContentDisposition;
import org.springframework.http.HttpHeaders;
import org.springframework.http.MediaType;
import org.springframework.http.ResponseEntity;
import org.springframework.web.bind.annotation.GetMapping;
import org.springframework.web.bind.annotation.PostMapping;
import org.springframework.web.bind.annotation.RequestBody;
import org.springframework.web.bind.annotation.RequestParam;
import org.springframework.web.bind.annotation.RestController;

import com.fasterxml.jackson.core.JsonProcessingException;
import com.fasterxml.jackson.databind.ObjectMapper;

@RestController
public class FusionController {
    private final FusionService fusionService;
    private final ObjectMapper objectMapper;

    public FusionController(FusionService fusionService, ObjectMapper objectMapper) {
        this.fusionService = fusionService;
        this.objectMapper = objectMapper;
    }

    @GetMapping("/api/dataset")
    public Map<String, Object> dataset() {
        return fusionService.datasetIndex();
    }

    @PostMapping("/api/preview")
    public Map<String, Object> preview(@RequestBody(required = false) Map<String, Object> body) {
        Selection selection = Selection.from(body);
        return fusionService.preview(selection.subject(), selection.activity(), selection.trial());
    }

    @PostMapping("/api/align")
    public Map<String, Object> align(@RequestBody(required = false) Map<String, Object> body) {
        Selection selection = Selection.from(body);
        return fusionService.alignSample(selection.subject(), selection.activity(), selection.trial());
    }

    @PostMapping("/api/fuse")
    public Map<String, Object> fuse(@RequestBody(required = false) Map<String, Object> body) {
        Selection selection = Selection.from(body);
        List<Double> weights = FusionService.weightsFrom(body == null ? null : body.get("weights"));
        Map<String, Object> alignment = fusionService.alignSample(selection.subject(), selection.activity(), selection.trial());
        return fusionService.fuseSample(alignment, weights);
    }

    @GetMapping("/api/export")
    public ResponseEntity<String> export(
            @RequestParam(defaultValue = "01") String subject,
            @RequestParam(defaultValue = "01") String activity,
            @RequestParam(defaultValue = "1") String trial) throws JsonProcessingException {
        Selection selection = new Selection(Selection.zfill(subject, 2), Selection.zfill(activity, 2), trial);
        Map<String, Object> alignment = fusionService.alignSample(selection.subject(), selection.activity(), selection.trial());
        Map<String, Object> result = Map.of(
                "sample", Map.of("subject", selection.subject(), "activity", selection.activity(), "trial", selection.trial()),
                "alignment", alignment,
                "fusion", fusionService.fuseSample(alignment, List.of(0.25, 0.25, 0.25, 0.25)));
        String json = objectMapper.writerWithDefaultPrettyPrinter().writeValueAsString(result);
        HttpHeaders headers = new HttpHeaders();
        headers.setContentDisposition(ContentDisposition.attachment()
                .filename("mex_result_%s_%s_%s.json".formatted(selection.subject(), selection.activity(), selection.trial()), StandardCharsets.UTF_8)
                .build());
        return ResponseEntity.ok().headers(headers).contentType(MediaType.APPLICATION_JSON).body(json);
    }

    private record Selection(String subject, String activity, String trial) {
        static Selection from(Map<String, Object> body) {
            body = body == null ? Map.of() : body;
            return new Selection(
                    zfill(String.valueOf(body.getOrDefault("subject", "01")), 2),
                    zfill(String.valueOf(body.getOrDefault("activity", "01")), 2),
                    String.valueOf(body.getOrDefault("trial", "1")));
        }

        static String zfill(String value, int width) {
            String clean = value == null ? "" : value.trim();
            return clean.length() >= width ? clean : "0".repeat(width - clean.length()) + clean;
        }
    }
}
