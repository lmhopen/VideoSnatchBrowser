import os
import json
import time
import logging
import base64
from pathlib import Path
from urllib.parse import urlparse

logger = logging.getLogger(__name__)

try:
    from cryptography.fernet import Fernet
    from cryptography.hazmat.primitives import hashes
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC
    CRYPTO_AVAILABLE = True
except ImportError:
    CRYPTO_AVAILABLE = False
    logger.warning("cryptography 未安装，Auth Vault 使用明文存储 (仅开发环境)")


def _derive_key(machine_id: str) -> bytes:
    if not CRYPTO_AVAILABLE:
        return b""
    from cryptography.fernet import Fernet as _F  # noqa: F811
    from cryptography.hazmat.primitives import hashes as _H
    from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC as _K
    kdf = _K(algorithm=_H.SHA256(), length=32, salt=b"vsnatch_vault", iterations=600000)
    return base64.urlsafe_b64encode(kdf.derive(machine_id.encode()))


def _machine_id() -> str:
    try:
        import uuid
        return str(uuid.UUID(int=uuid.getnode()))
    except Exception:
        return "VideoSnatchDefaultKey"


VAULT_DIR = Path(os.environ.get("VSNATCH_VAULT_DIR", Path.home() / ".videosnatch" / "vault"))


class CookieEntry:
    def __init__(self, domain: str, name: str, value: str,
                 path: str = "/", secure: bool = False,
                 http_only: bool = False, same_site: str = "",
                 expiry: float = 0):
        self.domain = domain
        self.name = name
        self.value = value
        self.path = path
        self.secure = secure
        self.http_only = http_only
        self.same_site = same_site
        self.expiry = expiry

    def is_expired(self) -> bool:
        return 0 < self.expiry < time.time()

    def to_cdp(self) -> dict:
        return {
            "name": self.name,
            "value": self.value,
            "domain": self.domain,
            "path": self.path,
            "secure": self.secure,
            "httpOnly": self.http_only,
            "sameSite": self.same_site or "Lax",
        }

    def to_dict(self) -> dict:
        return {
            "domain": self.domain,
            "name": self.name,
            "value": self.value,
            "path": self.path,
            "secure": self.secure,
            "httpOnly": self.http_only,
            "sameSite": self.same_site,
            "expiry": self.expiry,
        }

    @staticmethod
    def from_dict(d: dict):
        return CookieEntry(
            domain=d.get("domain", ""),
            name=d.get("name", ""),
            value=d.get("value", ""),
            path=d.get("path", "/"),
            secure=d.get("secure", False),
            http_only=d.get("httpOnly", False),
            same_site=d.get("sameSite", ""),
            expiry=d.get("expiry", 0),
        )

    @staticmethod
    def from_netscape(line: str):
        line = line.strip()
        if not line or line.startswith("#"):
            return None
        parts = line.split("\t")
        if len(parts) < 7:
            return None
        try:
            return CookieEntry(
                domain=parts[0],
                name=parts[5],
                value=parts[6],
                path=parts[2],
                secure=parts[3].upper() == "TRUE",
                expiry=float(parts[4]) if parts[4] else 0,
            )
        except (IndexError, ValueError):
            return None

    @staticmethod
    def from_browser_cookie(c: dict):
        return CookieEntry(
            domain=c.get("domain", "").lstrip("."),
            name=c.get("name", ""),
            value=c.get("value", ""),
            path=c.get("path", "/"),
            secure=c.get("secure", False),
            http_only=c.get("httpOnly", False),
            same_site=c.get("sameSite", ""),
            expiry=c.get("expires", 0),
        )


class AuthVault:
    def __init__(self, vault_dir=None):
        self._vault_dir = Path(vault_dir) if vault_dir else VAULT_DIR
        self._vault_dir.mkdir(parents=True, exist_ok=True)
        self._fernet: object = None
        if CRYPTO_AVAILABLE:
            from cryptography.fernet import Fernet as _F
            key = _derive_key(_machine_id())
            self._fernet = _F(key)

    def _domain_file(self, domain: str) -> Path:
        safe = domain.replace(".", "_").replace(":", "_")
        return self._vault_dir / f"{safe}.enc"

    def _encrypt(self, data: dict) -> bytes:
        raw = json.dumps(data, ensure_ascii=False, indent=2).encode("utf-8")
        if self._fernet is not None:
            return self._fernet.encrypt(raw)  # type: ignore
        return raw

    def _decrypt(self, blob: bytes) -> dict:
        if self._fernet is not None:
            try:
                raw = self._fernet.decrypt(blob)  # type: ignore
            except Exception:
                logger.warning("Auth Vault 解密失败，数据可能已损坏")
                return {}
            return json.loads(raw.decode("utf-8"))
        return json.loads(blob.decode("utf-8"))

    def save_cookies(self, domain: str, cookies: list):
        entries = [CookieEntry.from_browser_cookie(c).to_dict() for c in cookies]
        data = {
            "domain": domain,
            "updated_at": time.time(),
            "cookies": entries,
        }
        path = self._domain_file(domain)
        blob = self._encrypt(data)
        path.write_bytes(blob)
        logger.info(f"Auth Vault: 已保存 {len(entries)} 个 cookie 到 {domain}")

    def load_cookies(self, domain: str) -> list[CookieEntry]:
        path = self._domain_file(domain)
        if not path.exists():
            return []
        try:
            data = self._decrypt(path.read_bytes())
        except Exception as e:
            logger.error(f"Auth Vault 加载失败 {domain}: {e}")
            return []
        now = time.time()
        valid = []
        for c in data.get("cookies", []):
            entry = CookieEntry.from_dict(c)
            if entry.is_expired():
                continue
            valid.append(entry)
        return valid

    def list_domains(self) -> list[str]:
        domains = []
        for f in self._vault_dir.glob("*.enc"):
            domain = f.stem.replace("_", ".")
            domains.append(domain)
        return sorted(domains)

    def delete_domain(self, domain: str):
        path = self._domain_file(domain)
        if path.exists():
            path.unlink()
            logger.info(f"Auth Vault: 已删除 {domain} 的凭据")

    def import_netscape(self, filepath: str) -> int:
        count = 0
        domain_buckets: dict[str, list[CookieEntry]] = {}
        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                entry = CookieEntry.from_netscape(line)
                if entry:
                    domain_buckets.setdefault(entry.domain, []).append(entry)
                    count += 1
        for domain, entries in domain_buckets.items():
            cdp_cookies = [e.to_cdp() for e in entries]
            self.save_cookies(domain, cdp_cookies)
        logger.info(f"Auth Vault: 从 Netscape 文件导入 {count} 个 cookie")
        return count

    def export_netscape(self, filepath: str) -> int:
        count = 0
        with open(filepath, "w", encoding="utf-8") as f:
            f.write("# Netscape HTTP Cookie File\n")
            f.write("# 由 VideoSnatch Auth Vault 导出\n\n")
            for domain in self.list_domains():
                entries = self.load_cookies(domain)
                for e in entries:
                    f.write(f"{e.domain}\tTRUE\t{e.path}\t"
                            f"{'TRUE' if e.secure else 'FALSE'}\t"
                            f"{int(e.expiry) if e.expiry else 0}\t"
                            f"{e.name}\t{e.value}\n")
                    count += 1
        logger.info(f"Auth Vault: 导出 {count} 个 cookie 到 {filepath}")
        return count

    async def inject_to_context(self, context, domain: str = ""):
        if domain:
            entries = self.load_cookies(domain)
            if entries:
                cdp_cookies = [e.to_cdp() for e in entries]
                try:
                    await context.add_cookies(cdp_cookies)
                    logger.info(f"Auth Vault: 已向浏览器注入 {len(entries)} 个 cookie ({domain})")
                except Exception as e:
                    logger.error(f"Auth Vault 注入 cookie 失败 {domain}: {e}")
        else:
            for d in self.list_domains():
                await self.inject_to_context(context, d)

    async def extract_from_context(self, context, domain_filter: str = ""):
        try:
            cookies = await context.cookies()
        except Exception as e:
            logger.error(f"从浏览器提取 cookie 失败: {e}")
            return
        buckets: dict[str, list] = {}
        for c in cookies:
            dom = c.get("domain", "").lstrip(".")
            if domain_filter and domain_filter not in dom:
                continue
            buckets.setdefault(dom, []).append(c)
        for dom, cs in buckets.items():
            self.save_cookies(dom, cs)
        logger.info(f"Auth Vault: 从浏览器提取了 {len(cookies)} 个 cookie")
