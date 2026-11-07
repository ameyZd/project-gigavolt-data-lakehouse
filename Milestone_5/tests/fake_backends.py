"""In-memory stand-ins for MongoDB, MariaDB and ChromaDB so the pipeline can be tested offline.

``MiniCollection`` evaluates the subset of MongoDB query/aggregation features the retriever
uses ($match with equality/$in/$regex/$or/$gte/$lte, $group accumulators, $unwind, $sort,
$limit, $addFields with $size), which lets the real ``DocumentSource`` code run unchanged.
"""

from __future__ import annotations

import copy
import re
from datetime import datetime
from typing import Any

from gigavolt_rag.sources.base import ContextItem, SourceResult


# ----------------------------------------------------------------------------- MongoDB
def _get(doc: dict, path: str) -> Any:
    value: Any = doc
    for part in path.split("."):
        if not isinstance(value, dict):
            return None
        value = value.get(part)
    return value


def _match_value(actual: Any, cond: Any) -> bool:
    if isinstance(cond, dict) and any(k.startswith("$") for k in cond):
        for op, arg in cond.items():
            if op == "$in":
                if not any(_match_value(actual, a) for a in arg):
                    return False
            elif op == "$regex":
                values = actual if isinstance(actual, list) else [actual]
                if not any(isinstance(v, str) and re.search(arg, v) for v in values):
                    return False
            elif op == "$gte":
                if actual is None or actual < arg:
                    return False
            elif op == "$lte":
                if actual is None or actual > arg:
                    return False
            elif op == "$lt":
                if actual is None or actual >= arg:
                    return False
            elif op == "$ne":
                if actual == arg:
                    return False
            else:
                raise NotImplementedError(op)
        return True
    if isinstance(actual, list) and not isinstance(cond, list):
        return cond in actual
    return actual == cond


def matches(doc: dict, filter_: dict) -> bool:
    for key, cond in filter_.items():
        if key == "$or":
            if not any(matches(doc, sub) for sub in cond):
                return False
        elif key == "$text":
            words = [w.lower() for w in re.findall(r"\w+", cond["$search"]) if len(w) > 3]
            if not any(w in str(doc.get("notes", "")).lower() for w in words):
                return False
        elif not _match_value(_get(doc, key), cond):
            return False
    return True


def _eval(expr: Any, doc: dict) -> Any:
    if isinstance(expr, str) and expr.startswith("$"):
        return _get(doc, expr[1:])
    if isinstance(expr, dict):
        if "$cond" in expr:
            test, yes, no = expr["$cond"]
            return _eval(yes, doc) if _eval(test, doc) else _eval(no, doc)
        if "$gte" in expr:
            a, b = (_eval(x, doc) for x in expr["$gte"])
            return a is not None and a >= b
        if "$size" in expr:
            return len(_eval(expr["$size"], doc) or [])
        if "$arrayElemAt" in expr:
            array, index = expr["$arrayElemAt"]
            return array[_eval(index, doc)]
    return expr


def _sort(docs: list[dict], spec) -> list[dict]:
    items = spec.items() if isinstance(spec, dict) else spec
    for key, direction in reversed(list(items)):
        docs = sorted(docs, key=lambda d: (_get(d, key) is None, _get(d, key) if _get(d, key) is not None else 0),
                      reverse=direction == -1)
    return docs


def aggregate(docs: list[dict], pipeline: list[dict]) -> list[dict]:
    docs = [copy.deepcopy(d) for d in docs]
    for stage in pipeline:
        (op, arg), = stage.items()
        if op == "$match":
            docs = [d for d in docs if matches(d, arg)]
        elif op == "$unwind":
            path = arg["path"] if isinstance(arg, dict) else arg
            keep = isinstance(arg, dict) and arg.get("preserveNullAndEmptyArrays")
            field = path[1:]
            out = []
            for d in docs:
                values = d.get(field)
                if isinstance(values, list) and values:
                    for v in values:
                        nd = dict(d)
                        nd[field] = v
                        out.append(nd)
                elif keep:
                    nd = dict(d)
                    nd.pop(field, None)
                    out.append(nd)
            docs = out
        elif op == "$group":
            groups: dict[Any, dict] = {}
            order: list[Any] = []
            for d in docs:
                key = _eval(arg["_id"], d)
                hkey = repr(key)
                if hkey not in groups:
                    groups[hkey] = {"_id": key, "__docs": []}
                    order.append(hkey)
                groups[hkey]["__docs"].append(d)
            out = []
            for hkey in order:
                g = groups[hkey]
                row = {"_id": g["_id"]}
                for name, acc in arg.items():
                    if name == "_id":
                        continue
                    (aop, aexpr), = acc.items()
                    vals = [_eval(aexpr, d) for d in g["__docs"]]
                    if aop == "$sum":
                        row[name] = sum(v for v in vals if isinstance(v, (int, float)))
                    elif aop == "$min":
                        present = [v for v in vals if v is not None]
                        row[name] = min(present) if present else None
                    elif aop == "$max":
                        present = [v for v in vals if v is not None]
                        row[name] = max(present) if present else None
                    elif aop == "$avg":
                        present = [v for v in vals if isinstance(v, (int, float))]
                        row[name] = sum(present) / len(present) if present else None
                    elif aop == "$first":
                        row[name] = vals[0] if vals else None
                    elif aop == "$push":
                        row[name] = vals
                    elif aop == "$addToSet":
                        uniq = []
                        for v in vals:
                            if v is not None and v not in uniq:
                                uniq.append(v)
                        row[name] = uniq
                    else:
                        raise NotImplementedError(aop)
                out.append(row)
            docs = out
        elif op == "$sort":
            docs = _sort(docs, arg)
        elif op == "$limit":
            docs = docs[:arg]
        elif op == "$addFields":
            for d in docs:
                for name, expr in arg.items():
                    d[name] = _eval(expr, d)
        else:
            raise NotImplementedError(op)
    return docs


class MiniCursor:
    def __init__(self, docs):
        self.docs = docs

    def sort(self, key, direction=None):
        spec = [(key, direction)] if isinstance(key, str) else key
        spec = [(k, d) for k, d in spec if not isinstance(d, dict)]  # ignore textScore sort
        self.docs = _sort(self.docs, spec)
        return self

    def limit(self, n):
        self.docs = self.docs[:n]
        return self

    def __iter__(self):
        return iter(self.docs)


class _Admin:
    def command(self, *_args, **_kwargs):
        return {"ok": 1}


class _Client:
    admin = _Admin()


class _Database:
    client = _Client()


class MiniCollection:
    name = "bms_telemetry"
    database = _Database()

    def __init__(self, docs: list[dict]):
        self.docs = docs

    def aggregate(self, pipeline):
        return iter(aggregate(self.docs, pipeline))

    def find(self, filter_=None, projection=None):
        return MiniCursor([copy.deepcopy(d) for d in self.docs if matches(d, filter_ or {})])

    def find_one(self, filter_=None, projection=None, sort=None):
        docs = [d for d in self.docs if matches(d, filter_ or {})]
        if sort:
            docs = _sort(docs, sort)
        return copy.deepcopy(docs[0]) if docs else None

    def count_documents(self, filter_):
        return sum(1 for d in self.docs if matches(d, filter_))

    def distinct(self, key):
        return sorted({_get(d, key) for d in self.docs if _get(d, key) is not None})

    def list_indexes(self):
        return iter([{"name": "_id_", "key": {"_id": 1}}])


# ----------------------------------------------------------------------------- simple fakes
class FakeVector:
    """Keyword-overlap 'vector' search over Milestone 4 chunks (stands in for ChromaDB)."""

    source_type = "vector"

    def __init__(self, chunks: list[dict]):
        self.chunks = chunks

    def warmup(self):
        return None

    def retrieve_query(self, query, top_k, doc_types, stage):
        words = {w for w in re.findall(r"[a-z0-9_\-]+", query.lower()) if len(w) > 3}
        scored = []
        for chunk in self.chunks:
            if doc_types and chunk["document_type"] not in doc_types:
                continue
            text = chunk["text"].lower()
            score = sum(1 for w in words if w in text) / max(len(words), 1)
            if score > 0.15:
                scored.append((score, chunk))
        scored.sort(key=lambda pair: -pair[0])
        items = [ContextItem(
            source_type="vector", database="ChromaDB (fake)", collection="gigavolt_unstructured_v1",
            record_ids=[c["chunk_id"]], title=c["source_file"], text=c["text"], document_name=c["source_file"],
            page=c["page_number"], timestamp="indexed 2026-10-24T04:58:53Z", score=round(s, 3),
            verify={"engine": "chromadb", "ids": [c["chunk_id"]]}) for s, c in scored[:top_k]]
        return SourceResult("vector", items=items, elapsed_ms=2.0,
                            details={"embed_ms": 1.0, "search_ms": 1.0, "stage": stage})


class FakeSQLSource:
    """Replaces MariaDB with canned rows routed by the SQL text (exercises SQLSource formatting)."""

    def __init__(self, handler):
        self.handler = handler

    def __call__(self, sql, params=()):
        return self.handler(" ".join(sql.split()), tuple(params))


def to_datetime(value: str) -> datetime:
    return datetime.fromisoformat(value.replace("Z", "+00:00"))
