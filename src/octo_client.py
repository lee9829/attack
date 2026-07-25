from __future__ import annotations

import time
from typing import Any, Dict, List, Optional, Union

import requests

from .proxy_manager import Proxy


class OctoError(RuntimeError):
    pass


def normalize_profile_os(os_name: str) -> str:
    """Map UI aliases to Octo fingerprint.os values: win | mac | android."""
    o = (os_name or "win").strip().lower()
    if o in ("android", "and", "mobile", "phone", "a"):
        return "android"
    if o in ("mac", "macos", "osx", "darwin", "m"):
        return "mac"
    if o in ("win", "windows", "w", "pc", "desktop"):
        return "win"
    return "win"


class OctoClient:
    """
    Octo Browser Cloud API + Local Client API.

    Covers the common automation surface:
      profiles CRUD / patch / clone / bulk fields,
      tags, folders, proxies, fingerprint catalogs (GPU/screen/device),
      one-time profiles, local start/stop/active/username/version.
    """

    def __init__(
        self,
        api_token: str,
        cloud_base: str = "https://app.octobrowser.net/api/v2/automation",
        local_base: str = "http://127.0.0.1:58888/api",
        timeout: int = 180,
    ):
        self.api_token = api_token.strip()
        self.cloud_base = cloud_base.rstrip("/")
        self.local_base = local_base.rstrip("/")
        self.timeout = timeout
        self.session = requests.Session()
        self.session.headers.update(
            {
                "Content-Type": "application/json",
                "X-Octo-Api-Token": self.api_token,
            }
        )
        self.last_rate: Dict[str, Any] = {}

    # ---------- Cloud core ----------
    def _cloud(
        self,
        method: str,
        path: str,
        *,
        params: Optional[dict] = None,
        json_body: Any = None,
        retries: int = 4,
    ) -> Any:
        url = f"{self.cloud_base}{path}"
        last_err: Optional[Exception] = None
        for attempt in range(retries):
            try:
                resp = self.session.request(
                    method,
                    url,
                    params=params,
                    json=json_body,
                    timeout=self.timeout,
                )
                self._capture_rate(resp)
                if resp.status_code == 429:
                    retry_after = int(resp.headers.get("Retry-After", "60") or 60)
                    time.sleep(max(retry_after, 1))
                    continue
                if resp.status_code >= 400:
                    raise OctoError(
                        f"Cloud API {method} {path} HTTP {resp.status_code}: {resp.text[:500]}"
                    )
                data = resp.json()
                if isinstance(data, dict) and data.get("success") is False:
                    raise OctoError(
                        f"Cloud API 실패: {data.get('msg') or data.get('code') or data}"
                    )
                return data.get("data") if isinstance(data, dict) and "data" in data else data
            except (requests.RequestException, ValueError) as exc:
                last_err = exc
                time.sleep(2 * (attempt + 1))
        raise OctoError(f"Cloud API 요청 실패: {last_err}")

    def _capture_rate(self, resp: requests.Response) -> None:
        h = resp.headers
        self.last_rate = {
            "retry_after": h.get("Retry-After"),
            "limit_rpm": h.get("X-Ratelimit-Limit"),
            "limit_rph": h.get("X-Ratelimit-Limit-Hour"),
            "remaining_rpm": h.get("X-Ratelimit-Remaining"),
            "remaining_rph": h.get("X-Ratelimit-Remaining-Hour"),
            "reset": h.get("X-Ratelimit-Reset"),
        }

    def rate_limit_status(self) -> Dict[str, Any]:
        return dict(self.last_rate)

    def test_connection(self) -> int:
        data = self._cloud(
            "GET",
            "/profiles",
            params={"page_len": 10, "page": 0, "fields": "title"},
        )
        if isinstance(data, list):
            return len(data)
        return 0

    # ---------- Profiles ----------
    def list_profiles(
        self,
        search: str = "",
        page_len: int = 100,
        *,
        fields: str = "title,description,proxy,status,last_active,tags,os",
        ordering: str = "title",
    ) -> List[Dict[str, Any]]:
        page = 0
        all_items: List[Dict[str, Any]] = []
        while True:
            params: Dict[str, Any] = {
                "page_len": page_len,
                "page": page,
                "fields": fields,
                "ordering": ordering,
            }
            if search:
                params["search"] = search
            data = self._cloud("GET", "/profiles", params=params)
            items = data if isinstance(data, list) else []
            all_items.extend(items)
            if len(items) < page_len:
                break
            page += 1
            if page > 50:
                break
        return all_items

    def get_profile(self, uuid: str) -> Dict[str, Any]:
        data = self._cloud("GET", f"/profiles/{uuid}")
        if not isinstance(data, dict):
            raise OctoError(f"프로필 조회 실패: {data}")
        return data

    def find_profile_by_title(self, title: str) -> Optional[Dict[str, Any]]:
        for item in self.list_profiles(search=title):
            if str(item.get("title", "")).strip() == title:
                return item
        return None

    def build_fingerprint(
        self,
        os_name: str = "win",
        *,
        os_version: str = "",
        os_arch: str = "",
        screen: str = "",
        cpu: Optional[int] = None,
        ram: Optional[int] = None,
        renderer: str = "",
        noise: Optional[Dict[str, Any]] = None,
        device: str = "",
        extra: Optional[Dict[str, Any]] = None,
    ) -> Dict[str, Any]:
        os_key = normalize_profile_os(os_name)
        fp: Dict[str, Any] = {"os": os_key}
        if os_version:
            fp["os_version"] = str(os_version)
        if os_key == "mac" and os_arch:
            fp["os_arch"] = str(os_arch)  # arm | x86
        elif os_arch and os_key != "android":
            fp["os_arch"] = str(os_arch)
        if screen:
            fp["screen"] = str(screen)
        if cpu is not None:
            fp["cpu"] = int(cpu)
        if ram is not None:
            fp["ram"] = int(ram)
        if renderer:
            fp["renderer"] = str(renderer)
        if device and os_key == "android":
            # Android device model catalog value when available
            fp["device"] = str(device)
        if noise:
            fp["noise"] = dict(noise)
        if extra:
            for k, v in extra.items():
                if v is not None and k not in fp:
                    fp[k] = v
        return fp

    def create_profile(
        self,
        title: str,
        proxy: Optional[Proxy] = None,
        start_pages: Optional[List[str]] = None,
        os_name: str = "win",
        *,
        fingerprint: Optional[Dict[str, Any]] = None,
        tags: Optional[List[str]] = None,
        description: str = "",
        storage_options: Optional[Dict[str, bool]] = None,
        bookmarks: Optional[List[Dict[str, Any]]] = None,
        extensions: Optional[List[str]] = None,
        password: str = "",
        folder_uuid: str = "",
        extra_body: Optional[Dict[str, Any]] = None,
    ) -> str:
        os_key = normalize_profile_os(os_name)
        fp = dict(fingerprint) if fingerprint else self.build_fingerprint(os_key)
        if "os" not in fp:
            fp["os"] = os_key

        body: Dict[str, Any] = {
            "title": title[:90],
            "fingerprint": fp,
            "storage_options": storage_options
            or {
                "cookies": True,
                "passwords": True,
                "extensions": True,
                "localstorage": True,
                "history": False,
                "bookmarks": True,
                "serviceworkers": False,
            },
        }
        if description:
            body["description"] = description[:500]
        if proxy is not None:
            body["proxy"] = proxy.to_octo_inline()
        if start_pages:
            body["start_pages"] = start_pages[:20]
        if tags:
            # API accepts tag uuids or titles depending on plan; pass as list
            body["tags"] = list(tags)[:30]
        if bookmarks:
            body["bookmarks"] = bookmarks[:50]
        if extensions:
            body["extensions"] = extensions[:30]
        if password:
            body["password"] = password
        if folder_uuid:
            body["folder_uuid"] = folder_uuid
        if extra_body:
            body.update(extra_body)

        data = self._cloud("POST", "/profiles", json_body=body)
        uuid = data.get("uuid") if isinstance(data, dict) else None
        if not uuid:
            raise OctoError(f"프로필 생성 응답에 uuid 없음: {data}")
        return str(uuid)

    def create_mobile_profile(
        self,
        title: str,
        proxy: Optional[Proxy] = None,
        *,
        os_version: str = "14",
        device: str = "",
        tags: Optional[List[str]] = None,
        description: str = "mobile fingerprint + proxy matched",
        start_pages: Optional[List[str]] = None,
    ) -> str:
        fp = self.build_fingerprint(
            "android",
            os_version=os_version or "14",
            device=device,
        )
        return self.create_profile(
            title=title,
            proxy=proxy,
            os_name="android",
            fingerprint=fp,
            tags=tags or ["mobile", "auto"],
            description=description,
            start_pages=start_pages,
        )

    def update_profile(self, uuid: str, patch: Dict[str, Any]) -> Any:
        if not patch:
            return {}
        return self._cloud("PATCH", f"/profiles/{uuid}", json_body=patch)

    def update_profile_proxy(self, uuid: str, proxy: Proxy) -> None:
        self.update_profile(uuid, {"proxy": proxy.to_octo_inline()})

    def update_profile_start_pages(self, uuid: str, start_pages: List[str]) -> None:
        self.update_profile(uuid, {"start_pages": start_pages[:20]})

    def update_profile_tags(self, uuid: str, tags: List[str]) -> None:
        self.update_profile(uuid, {"tags": list(tags)[:30]})

    def update_profile_description(self, uuid: str, description: str) -> None:
        self.update_profile(uuid, {"description": (description or "")[:500]})

    def delete_profile(self, uuid: str) -> Any:
        return self._cloud("DELETE", f"/profiles/{uuid}")

    def delete_profiles(self, uuids: List[str]) -> List[Dict[str, Any]]:
        out: List[Dict[str, Any]] = []
        for u in uuids:
            try:
                self.delete_profile(u)
                out.append({"uuid": u, "ok": True})
            except Exception as exc:
                out.append({"uuid": u, "ok": False, "error": str(exc)})
            time.sleep(0.15)
        return out

    def clone_profile(
        self,
        uuid: str,
        *,
        title: str = "",
        count: int = 1,
    ) -> List[str]:
        """
        Clone profile fingerprint (storage contents not cloned).
        Endpoint shape varies by API version — try dedicated then fallback create-from-get.
        """
        count = max(1, min(int(count or 1), 20))
        body: Dict[str, Any] = {"uuid": uuid, "count": count}
        if title:
            body["title"] = title[:90]
        try:
            data = self._cloud("POST", "/profiles/clone", json_body=body)
            if isinstance(data, dict) and data.get("uuid"):
                return [str(data["uuid"])]
            if isinstance(data, list):
                return [str(x.get("uuid") or x) for x in data if x]
            if isinstance(data, dict) and isinstance(data.get("uuids"), list):
                return [str(u) for u in data["uuids"]]
        except OctoError:
            pass

        # Fallback: GET source + POST new with same fingerprint/proxy/tags
        src = self.get_profile(uuid)
        uuids: List[str] = []
        base_title = (title or str(src.get("title") or "clone"))[:80]
        for i in range(count):
            t = f"{base_title}-{i + 1}" if count > 1 else base_title
            fp = src.get("fingerprint") if isinstance(src.get("fingerprint"), dict) else None
            os_name = "win"
            if fp and fp.get("os"):
                os_name = str(fp.get("os"))
            new_uuid = self.create_profile(
                title=t,
                os_name=os_name,
                fingerprint=fp,
                tags=list(src.get("tags") or []) if isinstance(src.get("tags"), list) else None,
                description=str(src.get("description") or "cloned"),
                start_pages=list(src.get("start_pages") or []) or None,
                storage_options=src.get("storage_options")
                if isinstance(src.get("storage_options"), dict)
                else None,
            )
            # attach proxy if present
            proxy_data = src.get("proxy")
            if isinstance(proxy_data, dict) and proxy_data:
                try:
                    self.update_profile(new_uuid, {"proxy": proxy_data})
                except OctoError:
                    pass
            uuids.append(new_uuid)
            time.sleep(0.2)
        return uuids

    def profile_os_info(self, uuid: str) -> Dict[str, Any]:
        """Best-effort OS / mobile fingerprint summary for matching logs."""
        try:
            p = self.get_profile(uuid)
        except OctoError:
            return {"uuid": uuid, "os": "unknown", "mobile": False}
        fp = p.get("fingerprint") if isinstance(p.get("fingerprint"), dict) else {}
        os_name = str(
            (fp or {}).get("os") or p.get("os") or p.get("fingerprint_os") or "unknown"
        ).lower()
        mobile = os_name == "android" or "android" in os_name or "mobile" in os_name
        return {
            "uuid": uuid,
            "title": p.get("title"),
            "os": os_name,
            "os_version": (fp or {}).get("os_version") or "",
            "device": (fp or {}).get("device") or "",
            "screen": (fp or {}).get("screen") or "",
            "mobile": mobile,
            "proxy": p.get("proxy"),
            "tags": p.get("tags") or [],
            "status": p.get("status"),
        }

    # ---------- Tags / folders ----------
    def list_tags(self) -> List[Dict[str, Any]]:
        try:
            data = self._cloud("GET", "/tags")
            if isinstance(data, list):
                return data
            if isinstance(data, dict) and isinstance(data.get("data"), list):
                return data["data"]
        except OctoError:
            pass
        return []

    def create_tag(self, name: str, color: str = "") -> Any:
        body: Dict[str, Any] = {"name": (name or "")[:60]}
        if color:
            body["color"] = color
        return self._cloud("POST", "/tags", json_body=body)

    def ensure_tag(self, name: str) -> Optional[str]:
        name = (name or "").strip()
        if not name:
            return None
        for t in self.list_tags():
            if str(t.get("name") or t.get("title") or "").strip().lower() == name.lower():
                return str(t.get("uuid") or t.get("id") or name)
        try:
            created = self.create_tag(name)
            if isinstance(created, dict):
                return str(created.get("uuid") or created.get("id") or name)
        except OctoError:
            return name
        return name

    def list_folders(self) -> List[Dict[str, Any]]:
        try:
            data = self._cloud("GET", "/folders")
            if isinstance(data, list):
                return data
        except OctoError:
            pass
        return []

    # ---------- Proxies (Cloud) ----------
    def list_proxies(self, page_len: int = 100) -> List[Dict[str, Any]]:
        page = 0
        all_items: List[Dict[str, Any]] = []
        while True:
            try:
                data = self._cloud(
                    "GET",
                    "/proxies",
                    params={"page_len": page_len, "page": page},
                )
            except OctoError:
                break
            items = data if isinstance(data, list) else []
            all_items.extend(items)
            if len(items) < page_len:
                break
            page += 1
            if page > 30:
                break
        return all_items

    def create_proxy(
        self,
        proxy: Proxy,
        *,
        title: str = "",
    ) -> Any:
        body = proxy.to_octo_inline()
        if title:
            body["title"] = title[:90]
        elif proxy.raw_line:
            body["title"] = proxy.alias[:90]
        return self._cloud("POST", "/proxies", json_body=body)

    def delete_proxy(self, uuid: str) -> Any:
        return self._cloud("DELETE", f"/proxies/{uuid}")

    def check_proxy_cloud(self, proxy: Union[Proxy, Dict[str, Any]]) -> Dict[str, Any]:
        """Ask Octo cloud to validate a proxy if endpoint exists; else empty."""
        body = proxy.to_octo_inline() if isinstance(proxy, Proxy) else dict(proxy)
        for path in ("/proxies/check", "/proxy/check", "/proxies/test"):
            try:
                data = self._cloud("POST", path, json_body=body, retries=2)
                if isinstance(data, dict):
                    return data
                return {"raw": data}
            except OctoError:
                continue
        return {}

    # ---------- Fingerprint catalogs ----------
    def list_fp_screens(self) -> List[Any]:
        for path in ("/fingerprint/screens", "/fingerprints/screens", "/screens"):
            try:
                data = self._cloud("GET", path, retries=2)
                if isinstance(data, list):
                    return data
            except OctoError:
                continue
        return []

    def list_fp_gpus(self) -> List[Any]:
        for path in ("/fingerprint/gpus", "/fingerprints/gpus", "/gpus"):
            try:
                data = self._cloud("GET", path, retries=2)
                if isinstance(data, list):
                    return data
            except OctoError:
                continue
        return []

    def list_fp_devices(self) -> List[Any]:
        """Android device models catalog when available."""
        for path in (
            "/fingerprint/devices",
            "/fingerprints/devices",
            "/devices",
            "/fingerprint/android_devices",
        ):
            try:
                data = self._cloud("GET", path, retries=2)
                if isinstance(data, list):
                    return data
            except OctoError:
                continue
        return []

    def fingerprint_catalogs(self) -> Dict[str, List[Any]]:
        return {
            "screens": self.list_fp_screens(),
            "gpus": self.list_fp_gpus(),
            "devices": self.list_fp_devices(),
        }

    # ---------- Local ----------
    def _local(
        self,
        method: str,
        path: str,
        json_body: Any = None,
        timeout: Optional[int] = None,
    ) -> Any:
        url = f"{self.local_base}{path}"
        try:
            resp = requests.request(
                method,
                url,
                json=json_body,
                timeout=timeout or self.timeout,
            )
        except requests.RequestException as exc:
            raise OctoError(
                f"로컬 API 연결 실패 ({url}). Octo Browser 데스크톱 앱을 실행하고 로그인했는지 확인하세요. 원인: {exc}"
            ) from exc

        if resp.status_code >= 400:
            raise OctoError(f"Local API {method} {path} HTTP {resp.status_code}: {resp.text[:500]}")
        if not resp.text:
            return {}
        try:
            return resp.json()
        except ValueError:
            return {"raw": resp.text}

    def local_username(self) -> str:
        data = self._local("GET", "/username")
        return str(data.get("username", ""))

    def local_version(self) -> Dict[str, Any]:
        for path in ("/version", "/client/version", "/app/version"):
            try:
                data = self._local("GET", path, timeout=10)
                if isinstance(data, dict):
                    return data
                return {"raw": data}
            except OctoError:
                continue
        return {}

    def start_profile(
        self,
        uuid: str,
        *,
        headless: bool = False,
        timeout_sec: int = 120,
        only_local: bool = True,
        password: str = "",
        flags: Optional[List[str]] = None,
        debug_port: Union[bool, int] = True,
    ) -> Dict[str, Any]:
        body: Dict[str, Any] = {
            "uuid": uuid,
            "headless": headless,
            "debug_port": debug_port,
            "only_local": only_local,
            "timeout": timeout_sec,
            "flags": list(flags or []),
        }
        if password:
            body["password"] = password
        data = self._local("POST", "/profiles/start", json_body=body, timeout=timeout_sec + 30)
        if not isinstance(data, dict):
            raise OctoError(f"프로필 시작 응답 이상: {data}")
        code = data.get("code")
        if data.get("error") or (isinstance(code, int) and 1 <= code <= 9):
            raise OctoError(f"프로필 시작 실패: {data}")
        if not data.get("ws_endpoint") and not data.get("debug_port"):
            raise OctoError(f"ws_endpoint/debug_port 없음. 응답: {data}")
        return data

    def start_one_time_profile(
        self,
        *,
        proxy: Optional[Proxy] = None,
        os_name: str = "win",
        fingerprint: Optional[Dict[str, Any]] = None,
        headless: bool = False,
        timeout_sec: int = 120,
        only_local: bool = True,
        start_pages: Optional[List[str]] = None,
        title: str = "one-time",
    ) -> Dict[str, Any]:
        """
        One-time profile: create+start in one Local (or Cloud) call, auto-deleted on stop.
        Falls back to create + start if one-time endpoint unavailable.
        """
        os_key = normalize_profile_os(os_name)
        fp = dict(fingerprint) if fingerprint else self.build_fingerprint(os_key)
        body: Dict[str, Any] = {
            "title": (title or "one-time")[:90],
            "fingerprint": fp,
            "headless": headless,
            "debug_port": True,
            "only_local": only_local,
            "timeout": timeout_sec,
            "flags": [],
        }
        if proxy is not None:
            body["proxy"] = proxy.to_octo_inline()
        if start_pages:
            body["start_pages"] = start_pages[:20]

        # Prefer local one-time
        for path in ("/profiles/one_time", "/profiles/one-time", "/profiles/onetime"):
            try:
                data = self._local(
                    "POST", path, json_body=body, timeout=timeout_sec + 40
                )
                if isinstance(data, dict) and (
                    data.get("ws_endpoint") or data.get("debug_port")
                ):
                    data["one_time"] = True
                    return data
            except OctoError:
                continue

        # Fallback: persistent create + start (caller may delete later)
        uuid = self.create_profile(
            title=title or f"ot-{int(time.time())}",
            proxy=proxy,
            os_name=os_key,
            fingerprint=fp,
            start_pages=start_pages,
            description="fallback one-time",
        )
        data = self.start_profile(
            uuid, headless=headless, timeout_sec=timeout_sec, only_local=only_local
        )
        data["one_time"] = False
        data["uuid"] = data.get("uuid") or uuid
        return data

    def stop_profile(self, uuid: str, force: bool = False) -> None:
        path = "/profiles/force_stop" if force else "/profiles/stop"
        try:
            self._local("POST", path, json_body={"uuid": uuid})
        except OctoError:
            if not force:
                self._local("POST", "/profiles/force_stop", json_body={"uuid": uuid})
            else:
                raise

    def force_stop_profile(self, uuid: str) -> None:
        self.stop_profile(uuid, force=True)

    def list_active_profiles(self) -> List[Dict[str, Any]]:
        data = self._local("GET", "/profiles/active")
        return data if isinstance(data, list) else []

    def is_profile_active(self, uuid: str) -> bool:
        for active in self.list_active_profiles():
            if str(active.get("uuid")) == str(uuid):
                return True
        return False

    def extract_connection_ip(self, start_response: Dict[str, Any]) -> Dict[str, str]:
        """Parse Local start response for exit IP / geo."""
        ip = ""
        country = ""
        city = ""
        conn = start_response.get("connection_data")
        if isinstance(conn, dict):
            ip = str(conn.get("ip") or conn.get("external_ip") or conn.get("public_ip") or "")
            country = str(conn.get("country") or conn.get("country_code") or "")
            city = str(conn.get("city") or "")
        if not ip:
            ip = str(start_response.get("ip") or "")
        return {"ip": ip.strip(), "country": country.strip(), "city": city.strip()}

    def match_profile_mobile_ip(
        self,
        *,
        profile_info: Dict[str, Any],
        exit_ip: str,
        api_ip: str = "",
        proxy: Optional[Proxy] = None,
        prefer_mobile: bool = True,
    ) -> Dict[str, Any]:
        """
        Score / report whether mobile fingerprint + proxy exit IP look consistent.
        Does not claim carrier-grade mobile detection — logs best-effort matching.
        """
        os_name = str(profile_info.get("os") or "").lower()
        mobile_fp = bool(profile_info.get("mobile")) or os_name == "android"
        ips = [p for p in (exit_ip, api_ip) if p and p not in ("미확인", "n/a", "pending")]
        unique_ips = list(dict.fromkeys(ips))
        ip_agree = True
        if len(unique_ips) >= 2:
            ip_agree = unique_ips[0] == unique_ips[1]

        proxy_hint = ""
        if proxy is not None:
            proxy_hint = f"{proxy.host}:{proxy.port}"
            raw = (proxy.raw_line or proxy.login or "").lower()
            looks_mobile_proxy = any(
                k in raw for k in ("mobile", "4g", "5g", "lte", "cellular", "mobi")
            )
        else:
            looks_mobile_proxy = False

        score = 0
        notes: List[str] = []
        if mobile_fp:
            score += 40
            notes.append("fingerprint=android/mobile")
        elif prefer_mobile:
            notes.append("fingerprint≠mobile (win/mac)")
        else:
            score += 20
            notes.append("desktop fingerprint OK")

        if unique_ips:
            score += 30
            notes.append(f"exit_ip={unique_ips[0]}")
        else:
            notes.append("exit_ip=미확인")

        if ip_agree and len(unique_ips) >= 1:
            score += 15
            notes.append("api_ip↔browser_ip 일치" if len(ips) >= 2 else "단일 IP 소스")
        elif not ip_agree:
            notes.append(f"IP 불일치 api={api_ip} browser={exit_ip}")

        if looks_mobile_proxy and mobile_fp:
            score += 15
            notes.append("proxy 문자열에 mobile 힌트 + android FP")
        elif looks_mobile_proxy and not mobile_fp:
            notes.append("proxy mobile 힌트 있으나 desktop FP")
        elif mobile_fp and not looks_mobile_proxy:
            notes.append("android FP + 일반 프록시 (mobile 전용 아님 가능)")

        status = "ok" if score >= 70 else ("partial" if score >= 40 else "weak")
        return {
            "status": status,
            "score": score,
            "mobile_fingerprint": mobile_fp,
            "os": os_name,
            "exit_ip": exit_ip or (unique_ips[0] if unique_ips else ""),
            "api_ip": api_ip,
            "ip_agree": ip_agree,
            "proxy": proxy_hint,
            "looks_mobile_proxy": looks_mobile_proxy,
            "notes": notes,
            "summary": f"match={status} score={score} · " + " · ".join(notes),
        }
