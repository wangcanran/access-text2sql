"""Gold-set predictor using the upstream RBAC prompts and explicit Qwen settings."""
import hashlib
import json
import os
import time

from predict_rbac_endpoint import AuditedClient, clean_final_response


class GoldQwenClient(AuditedClient):
    def _build_payload(self, prompt, max_tokens=2048, temperature=0.0):
        payload = super()._build_payload(prompt, max_tokens, temperature)
        if os.environ.get('RBAC_QWEN_CLEAN') == '1':
            payload['chat_template_kwargs'] = {'enable_thinking': False}
        else:
            payload.update({
            "temperature": 0.0,
            "top_p": 0.8,
            "top_k": 20,
            "min_p": 0.0,
            "presence_penalty": 1.5,
            "repetition_penalty": 1.0,
            "seed": 20261030,
            "chat_template_kwargs": {"enable_thinking": False},
            })
        self._local.request_parameters = {
            key: value for key, value in payload.items() if key != "messages"
        }
        return payload

    def _extract_response(self, data):
        path = os.environ.get("RBAC_RESPONSE_AUDIT")
        if path:
            record = {
                "time": time.time(),
                "prompt_sha256": self._local.prompt_hash,
                "request_parameters": self._local.request_parameters,
                "response": data,
            }
            with self._audit_lock, open(path, "a", encoding="utf8") as stream:
                stream.write(json.dumps(record, ensure_ascii=False) + "\n")
        if data.get("choices", [{}])[0].get("finish_reason") == "length":
            raise RuntimeError("Generation truncated at max_tokens; see response audit")
        message = data["choices"][0]["message"]
        content = message.get("content")
        if content is None or not content.strip():
            return "Error: Model emitted no final answer"
        return AuditedClient._extract_response(self, data)


if __name__ == "__main__":
    from predict import predict_cloud

    predict_cloud.CloudAPIClient = GoldQwenClient
    predict_cloud.clean_model_response = clean_final_response
    predict_cloud.main()
