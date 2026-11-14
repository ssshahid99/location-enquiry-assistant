"""OpenAPI YAML -> one section per operation (params, body, responses), plus an overview section."""
import json

import yaml


def _resolve(spec: dict, node, depth=0):
    """Resolve local $refs (bounded depth to survive cycles)."""
    if depth > 4:
        return {}
    if isinstance(node, dict):
        if "$ref" in node and isinstance(node["$ref"], str) and node["$ref"].startswith("#/"):
            cur = spec
            for p in node["$ref"][2:].split("/"):
                cur = cur.get(p, {}) if isinstance(cur, dict) else {}
            return _resolve(spec, cur, depth + 1)
        return {k: _resolve(spec, v, depth + 1) for k, v in node.items()}
    if isinstance(node, list):
        return [_resolve(spec, v, depth + 1) for v in node]
    return node


def _schema_summary(s: dict) -> str:
    if not s:
        return ""
    keep = {k: s[k] for k in ("type", "format", "enum", "default", "minimum", "maximum", "maxItems",
                              "minItems", "description", "example") if k in s}
    if s.get("type") == "object" and "properties" in s:
        keep["properties"] = {k: _schema_summary(v) if isinstance(v, dict) else v
                              for k, v in list(s["properties"].items())[:25]}
    if s.get("type") == "array" and "items" in s:
        keep["items"] = _schema_summary(s["items"])
    return json.dumps(keep, ensure_ascii=False) if keep else ""


def parse_openapi(raw: str) -> tuple[str, list[dict]]:
    spec = yaml.safe_load(raw)
    info = spec.get("info", {})
    title = info.get("title", "OpenAPI")
    servers = ", ".join(s.get("url", "") for s in spec.get("servers", []))
    sections = [{"section": "overview",
                 "text": f"{title} {info.get('version', '')}\n{info.get('description', '')}\nServers: {servers}".strip()}]
    for path, item in spec.get("paths", {}).items():
        shared = item.get("parameters", [])
        for method, op in item.items():
            if method not in ("get", "post", "put", "delete", "patch") or not isinstance(op, dict):
                continue
            op = _resolve(spec, op)
            lines = [f"{method.upper()} {path}", op.get("summary", ""), op.get("description", "")]
            params = [_resolve(spec, p) for p in shared] + op.get("parameters", [])
            if params:
                lines.append("Parameters:")
                for p in params:
                    lines.append(f"- {p.get('name')} ({p.get('in')}, {'required' if p.get('required') else 'optional'}): "
                                 f"{p.get('description', '')} {_schema_summary(p.get('schema', {}))}".strip())
            body = (op.get("requestBody") or {}).get("content", {})
            for ctype, c in body.items():
                lines.append(f"Request body ({ctype}): {_schema_summary(c.get('schema', {}))}")
            for code, r in (op.get("responses") or {}).items():
                lines.append(f"Response {code}: {r.get('description', '') if isinstance(r, dict) else ''}")
            sections.append({"section": f"{method.upper()} {path}",
                             "text": "\n".join(l for l in lines if l).strip()})
    return title, sections
