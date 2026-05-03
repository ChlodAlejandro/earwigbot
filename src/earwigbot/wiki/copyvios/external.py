import gzip
import io
import urllib.request
from abc import ABC
from urllib.error import URLError
from urllib.request import OpenerDirector

from werkzeug.sansio.request import Request

from earwigbot import exceptions


class ExternalService(ABC):
    """Abstract class for any class that calls an external service with HTTP."""

    name: str = "ExternalService"
    opener: OpenerDirector

    def __init__(
        self, opener: OpenerDirector
    ) -> None:
        self.opener = opener

    def _open(self, url: str | Request, data = None) -> bytes:
        """Open a URL (like urlopen) and try to return its contents."""
        try:
            response = self.opener.open(url) if isinstance(url, Request) else self.opener.open(url)
            result = response.read()
        except (OSError, URLError) as exc:
            raise exceptions.CopyvioCheckError(f"{self.name} Error: {exc}")

        if response.headers.get("Content-Encoding") == "gzip":
            stream = io.BytesIO(result)
            gzipper = gzip.GzipFile(fileobj=stream)
            result = gzipper.read()

        code = response.getcode()
        if code != 200:
            raise exceptions.CopyvioCheckError(
                f"{self.name} Error: got response code '{code}':\n{result}'"
            )

        return result