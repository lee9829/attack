# -*- coding: utf-8 -*-
"""
Bulk own-site target lists (1k~100k+ lines).

Accepts:
  - domain.com
  - www.domain.com/path
  - https://domain.com/promo/1
  - path-only /promo/.*  (regex if looks like regex)
  - mixed lists with comments (#)

Output is used by search_flow for SERP matching + optional direct hits.
"""
from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Set, Tuple
from urllib.parse import urlparse


def _norm_host(value: str) -> str:
    v = (value or "").strip().lower()
    if not v:
        return ""
    if "://" in v:
        try:
            v = urlparse(v).netloc or v
        except Exception:
            pass
    v = v.split("/")[0].split(":")[0]
    if v.startswith("www."):
        v = v[4:]
    return v


def _looks_like_regex(s: str) -> bool:
    s = (s or "").strip()
    if not s:
        return False
    if s.lower().startswith("re:"):
        return True
    return bool(re.search(r"[\\^$*+?{}\[\]|()]", s))


@dataclass
class BulkTargets:
    domains: List[str] = field(default_factory=list)
    domain_set: Set[str] = field(default_factory=set)
    # exact paths (normalized /foo/bar)
    paths_exact: Set[str] = field(default_factory=set)
    # plain path prefixes
    paths_prefix: List[str] = field(default_factory=list)
    # regex patterns as strings
    path_regexes: List[str] = field(default_factory=list)
    # full URL strings for exact/contains match
    full_urls: List[str] = field(default_factory=list)
    full_url_set: Set[str] = field(default_factory=set)
    # host -> list of paths from that host's URLs
    host_paths: Dict[str, List[str]] = field(default_factory=dict)
    raw_count: int = 0
    parse_errors: int = 0

    def stats(self) -> Dict[str, int]:
        return {
            "raw_lines": self.raw_count,
            "domains": len(self.domains),
            "paths_exact": len(self.paths_exact),
            "paths_prefix": len(self.paths_prefix),
            "path_regexes": len(self.path_regexes),
            "full_urls": len(self.full_urls),
            "parse_errors": self.parse_errors,
        }


def _norm_path(path: str) -> str:
    p = (path or "").strip()
    if not p:
        return ""
    if not p.startswith("/"):
        p = "/" + p
    # strip query/fragment if leaked
    p = p.split("?")[0].split("#")[0]
    if len(p) > 1:
        p = p.rstrip("/")
    return p or "/"


def parse_bulk_line(line: str) -> Optional[Dict[str, str]]:
    """
    Parse one target line into kind + values.
    Returns dict: kind = domain|url|path|regex , host, path, url, pattern
    """
    raw = (line or "").strip()
    if not raw or raw.startswith("#"):
        return None
    # strip common prefixes
    for pref in ("url:", "site:", "target:", "link:"):
        if raw.lower().startswith(pref):
            raw = raw[len(pref) :].strip()

    # pure regex path
    if raw.lower().startswith("re:") or (
        raw.startswith("/") and _looks_like_regex(raw)
    ):
        pat = raw[3:].strip() if raw.lower().startswith("re:") else raw
        return {"kind": "regex", "pattern": pat, "path": "", "host": "", "url": ""}

    # full URL
    if "://" in raw or raw.startswith("//"):
        u = raw if "://" in raw else "https:" + raw
        try:
            p = urlparse(u)
        except Exception:
            return None
        host = _norm_host(p.netloc)
        path = _norm_path(p.path or "/")
        if p.query:
            # keep full URL with query for exact match
            full = f"{p.scheme or 'https'}://{p.netloc}{p.path or '/'}"
            if p.query:
                full = f"{full}?{p.query}"
        else:
            full = f"{p.scheme or 'https'}://{host}{path}"
        return {
            "kind": "url",
            "host": host,
            "path": path,
            "url": full,
            "pattern": "",
        }

    # host/path without scheme: example.com/foo/bar
    if "/" in raw and not raw.startswith("/"):
        host_part, _, rest = raw.partition("/")
        host = _norm_host(host_part)
        if "." in host and not host.replace(".", "").isdigit():
            path = _norm_path("/" + rest)
            if _looks_like_regex(path):
                return {
                    "kind": "regex",
                    "pattern": path,
                    "host": host,
                    "path": path,
                    "url": f"https://{host}{path}",
                }
            return {
                "kind": "url",
                "host": host,
                "path": path,
                "url": f"https://{host}{path}",
                "pattern": "",
            }

    # path only
    if raw.startswith("/"):
        if _looks_like_regex(raw):
            return {"kind": "regex", "pattern": raw, "path": raw, "host": "", "url": ""}
        return {
            "kind": "path",
            "path": _norm_path(raw),
            "host": "",
            "url": "",
            "pattern": "",
        }

    # bare domain
    host = _norm_host(raw)
    if host and "." in host:
        return {"kind": "domain", "host": host, "path": "", "url": "", "pattern": ""}

    return None


def parse_bulk_text(text: str) -> BulkTargets:
    bt = BulkTargets()
    if not text:
        return bt
    domains: List[str] = []
    dset: Set[str] = set()
    for line in text.replace("\r", "\n").split("\n"):
        bt.raw_count += 1
        if not line.strip() or line.strip().startswith("#"):
            bt.raw_count -= 1  # don't count blanks in raw useful? keep simple
            if not line.strip() or line.strip().startswith("#"):
                continue
        item = parse_bulk_line(line)
        if not item:
            if line.strip() and not line.strip().startswith("#"):
                bt.parse_errors += 1
            continue
        kind = item["kind"]
        if kind == "domain":
            h = item["host"]
            if h and h not in dset:
                dset.add(h)
                domains.append(h)
        elif kind == "url":
            h = item["host"]
            path = item["path"] or "/"
            url = item["url"]
            if h and h not in dset:
                dset.add(h)
                domains.append(h)
            if path:
                bt.paths_exact.add(path)
                bt.host_paths.setdefault(h, []).append(path)
            if url:
                u_low = url.lower().rstrip("/")
                if u_low not in bt.full_url_set:
                    bt.full_url_set.add(u_low)
                    bt.full_urls.append(url)
        elif kind == "path":
            p = item["path"]
            if p:
                bt.paths_exact.add(p)
                if p != "/":
                    bt.paths_prefix.append(p)
        elif kind == "regex":
            pat = item["pattern"]
            if pat and pat not in bt.path_regexes:
                bt.path_regexes.append(pat)
            h = item.get("host") or ""
            if h and h not in dset:
                dset.add(h)
                domains.append(h)

    bt.domains = domains
    bt.domain_set = dset
    # unique prefixes
    seen_p: Set[str] = set()
    prefs = []
    for p in bt.paths_prefix:
        if p not in seen_p:
            seen_p.add(p)
            prefs.append(p)
    bt.paths_prefix = prefs
    return bt


def load_bulk_files(base_dir: Path, names: Optional[List[str]] = None) -> BulkTargets:
    """Load and merge urls.txt / sites.txt / targets_urls.txt / paths.txt."""
    names = names or [
        "urls.txt",
        "sites.txt",
        "targets_urls.txt",
        "paths.txt",
        "domains.txt",
    ]
    chunks: List[str] = []
    for name in names:
        p = Path(base_dir) / name
        if not p.is_file():
            continue
        raw = None
        for enc in ("utf-8-sig", "utf-8", "cp949", "euc-kr"):
            try:
                raw = p.read_text(encoding=enc)
                break
            except UnicodeDecodeError:
                continue
        if raw is None:
            raw = p.read_text(encoding="utf-8", errors="replace")
        chunks.append(raw)
    return parse_bulk_text("\n".join(chunks))


def apply_bulk_to_search_flow(
    sf: Dict[str, Any],
    bulk: BulkTargets,
    *,
    merge: bool = True,
) -> Dict[str, Any]:
    """Merge bulk targets into search_flow config keys."""
    sf = dict(sf or {})
    if not bulk.domains and not bulk.paths_exact and not bulk.path_regexes and not bulk.full_urls:
        return sf

    if merge:
        existing = list(sf.get("allowed_domains") or [])
        seen = {_norm_host(x) for x in existing}
        for d in bulk.domains:
            if d not in seen:
                existing.append(d)
                seen.add(d)
        sf["allowed_domains"] = existing
        if not str(sf.get("target_domain") or "").strip() and existing:
            sf["target_domain"] = existing[0]
        # domains_text: don't dump 10k into UI every time — keep file-backed hint
        if not str(sf.get("domains_text") or "").strip():
            sf["domains_text"] = "\n".join(existing[:50])
            if len(existing) > 50:
                sf["domains_text"] += f"\n# … +{len(existing)-50} more from bulk files"
    else:
        sf["allowed_domains"] = list(bulk.domains)
        if bulk.domains:
            sf["target_domain"] = bulk.domains[0]

    # paths → path_targets + path_regex
    paths = list(sf.get("path_targets") or [])
    for p in sorted(bulk.paths_exact):
        if p not in paths:
            paths.append(p)
    for p in bulk.paths_prefix:
        if p not in paths:
            paths.append(p)
    sf["path_targets"] = paths
    sf["path_targets_text"] = "\n".join(paths[:500])

    regs = list(sf.get("path_regexes") or [])
    for r in bulk.path_regexes:
        if r not in regs:
            regs.append(r)
    # also treat long path lists as optional exact via path_exact_set side channel
    sf["path_regexes"] = regs
    sf["path_regex_text"] = "\n".join(regs)
    if regs and not sf.get("path_regex"):
        sf["path_regex"] = regs[0]

    sf["bulk_full_urls"] = bulk.full_urls[:50000]
    sf["bulk_paths_exact"] = sorted(bulk.paths_exact)[:50000]
    sf["_bulk_domain_set"] = list(bulk.domain_set)
    sf["require_domain"] = True
    if regs or paths or bulk.full_urls:
        sf["require_regex"] = bool(sf.get("require_regex", bool(regs)))
    # performance flag
    sf["bulk_mode"] = True
    sf["bulk_stats"] = bulk.stats()
    return sf


def shard_list(items: List[Any], shard_index: int, shard_count: int) -> List[Any]:
    """Distribute items across workers (job_index based)."""
    if not items:
        return []
    shard_count = max(1, int(shard_count))
    shard_index = max(0, int(shard_index)) % shard_count
    return [x for i, x in enumerate(items) if i % shard_count == shard_index]


def host_in_bulk(host: str, domain_set: Set[str]) -> bool:
    h = _norm_host(host)
    if not h or not domain_set:
        return False
    if h in domain_set:
        return True
    # subdomain: a.b.example.com → example.com
    parts = h.split(".")
    for i in range(1, len(parts) - 1):
        cand = ".".join(parts[i:])
        if cand in domain_set:
            return True
    return False


def url_matches_bulk(
    real_url: str,
    *,
    domain_set: Set[str],
    paths_exact: Set[str],
    path_prefixes: List[str],
    path_regexes: List[re.Pattern],
    full_url_set: Set[str],
    require_path: bool = False,
) -> Tuple[bool, str]:
    try:
        p = urlparse(real_url)
    except Exception:
        return False, "bad_url"
    host = _norm_host(p.netloc)
    if domain_set and not host_in_bulk(host, domain_set):
        return False, "foreign_host"
    path = _norm_path(p.path or "/")
    full = real_url.lower().split("#")[0].rstrip("/")
    if full_url_set and full in full_url_set:
        return True, "full_url"
    # also without scheme variants
    if full_url_set:
        bare = full.split("://", 1)[-1]
        for u in list(full_url_set)[:0]:  # no-op keep type
            pass
        if any(bare == x.split("://", 1)[-1].rstrip("/") for x in [full]):
            if bare in {x.split("://", 1)[-1].rstrip("/") for x in full_url_set}:
                return True, "full_url_bare"

    path_ok = False
    reason = "domain_only"
    if paths_exact and path in paths_exact:
        path_ok = True
        reason = "path_exact"
    if not path_ok and path_prefixes:
        for pref in path_prefixes:
            if path == pref or path.startswith(pref + "/"):
                path_ok = True
                reason = "path_prefix"
                break
    if not path_ok and path_regexes:
        for rx in path_regexes:
            if rx.search(path) or rx.search(real_url):
                path_ok = True
                reason = "path_regex"
                break

    if require_path and (paths_exact or path_prefixes or path_regexes or full_url_set):
        if not path_ok and full not in full_url_set:
            # check bare full set
            bare = real_url.lower().split("://", 1)[-1].split("#")[0].rstrip("/")
            if bare not in {x.split("://", 1)[-1].rstrip("/") for x in full_url_set}:
                return False, "path_not_in_list"
            return True, "full_url_bare"
        return True, reason if path_ok else "full_url"

    return True, reason if path_ok else "domain"
