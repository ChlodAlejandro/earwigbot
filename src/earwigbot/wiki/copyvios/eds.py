import html
import json
import logging
from typing import Any, Generator
from urllib.request import OpenerDirector, Request

from earwigbot import exceptions
from earwigbot.wiki.copyvios.external import ExternalService
from earwigbot.wiki.copyvios.parsers import PDFParser, XMLParser, get_parser
from earwigbot.wiki.copyvios.types import OpenedURL, Source
from earwigbot.wiki.copyvios.xmlutils import XMLUtils


class EDSHelper(ExternalService):
    """EBSCO Discovery Service helper class.

    This class handles authentication, search, and full text retrieval with
    the EBSCO Discovery Service.

    API documentation can be found in https://developer.ebsco.com/eds-api/docs
    """

    name = "EDS"

    credentials: dict[str, str]
    domains: list[str]

    def __init__(self, eds_config: dict[str, dict[str, str]], opener: OpenerDirector):
        super().__init__(opener)

        if eds_config is None:
            raise ValueError("EDS configuration is required when EDS is used")
        if not isinstance(eds_config, dict):
            raise TypeError("EDS configuration must be a dict")

        if "credentials" not in eds_config:
            raise TypeError("EDS configuration missing credentials")
        self.credentials = eds_config["credentials"]
        if not isinstance(self.credentials, dict):
            raise TypeError("EDS configuration must be a dict")
        else:
            if not isinstance(self.credentials.get("api_base", None), str):
                raise TypeError("EDS configuration missing string: 'credentials.api_base'")
            if not isinstance(self.credentials.get("profile", None), str):
                raise TypeError("EDS configuration missing string: 'credentials.profile'")

            # Technically not required, but we'll support it anyway in case EBSCO switch us over to IP-based auth.
            if not self.credentials.get("proxy", False):
                # Not using IP-based authentication
                if not isinstance(self.credentials.get("user_id", None), str):
                    raise TypeError("EDS configuration missing string: 'credentials.user_id'")
                if not isinstance(self.credentials.get("password", None), str):
                    raise TypeError("EDS configuration missing string: 'credentials.password'")

        if "domains" not in eds_config:
            raise TypeError("EDS configuration missing domains")
        self.domains = eds_config["domains"]
        if not isinstance(self.domains, list):
            raise TypeError("EDS configuration must be a list")
        for domain in self.domains:
            if not isinstance(domain, str):
                raise TypeError(f"EDS configuration contains non-string value: {domain}")

        self._config = eds_config
        self._logger = logging.getLogger("eds")

    def _redact_token(self, token: str | None) -> str:
        if not token:
            return "<none>"
        if len(token) <= 8:
            return "<redacted>"
        return f"{token[:4]}...{token[-4:]}"

    def _build_get_request(self, path: str) -> Request:
        domain = self.credentials.get("domain", "eds-api.ebscohost.com")
        url = f"https://{domain.rstrip('/')}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json"
        }
        print(f"EDS GET request: path={path} url={url}")
        return Request(url, headers=headers)

    def _build_post_request(
        self,
        path: str,
        data: dict[str, Any],
        auth_token: str | None = None,
        session_token: str | None = None
    ) -> Request:
        data_charset = "utf-8"

        domain = self.credentials.get("domain", "eds-api.ebscohost.com")
        url = f"https://{domain.rstrip('/')}/{path.lstrip('/')}"
        headers = {
            "Accept": "application/json",
            "Content-Type": f"application/json; charset={data_charset}",
        }
        if not auth_token is None:
            headers["X-AuthenticationToken"] = auth_token
        if not session_token is None:
            headers["X-SessionToken"] = session_token
        print(
            "EDS POST request: "
            f"path={path} url={url} "
            f"auth={self._redact_token(auth_token)} "
            f"session={self._redact_token(session_token)}"
        )
        return Request(url, json.dumps(data).encode(data_charset), headers, method="POST")

    def _get_auth_info(self) -> tuple[str, int]:
        """Get the authentication token and timeout."""
        print("EDS auth: requesting auth token")
        result = self._open(self._build_post_request("/authservice/rest/uidauth", {
            "UserId": self.credentials.get("user_id"),
            "Password": self.credentials.get("password"),
        }))

        try:
            res = json.loads(result)
        except ValueError as exc:
            raise exceptions.EDSQueryError("EDS error: JSON could not be decoded") from exc

        try:
            auth_token = res["AuthToken"]
            auth_timeout = res["AuthTimeout"]
            print(f"EDS auth: received token={self._redact_token(auth_token)} timeout={auth_timeout}")
            return auth_token, auth_timeout
        except KeyError as exc:
            raise exceptions.EDSQueryError("Failed to get authentication token for EDS") from exc


    def _get_session_token(self, auth_token: str) -> str:
        """Get an EDS session token. This requires an authentication token, which can be obtained
         with py:meth:`~EDSHelper._get_auth_info`.
        """
        print(f"EDS session: requesting session token with auth={self._redact_token(auth_token)}")
        result = self._open(self._build_post_request("/edsapi/rest/createsession", {
            "Profile": self.credentials["profile"],
            "Org": "WikimediaFoundation"
        }, auth_token))

        try:
            res = json.loads(result)
        except ValueError as exc:
            raise exceptions.EDSQueryError("EDS error: JSON could not be decoded") from exc

        try:
            session_token = res["SessionToken"]
            print(f"EDS session: received session token={self._redact_token(session_token)}")
            return session_token
        except KeyError as exc:
            raise exceptions.EDSQueryError("Failed to get session token for EDS") from exc


    def xml_to_text(self, xml: str | bytes) -> str:
        """Converts XML expressions (HTML or otherwise) to just plain text. Used for titles."""
        return XMLUtils.clean_soup(XMLUtils.get_soup(xml))


    def start_session(self) -> tuple[str, str]:
        """Start an EDS session. This returns a tuple containing the authentication
        token and session token, respectively. Both are required to make authenticated
        requests to the EDS API.
        """
        print("EDS session: starting")
        auth_token, _ = self._get_auth_info()
        session_token = self._get_session_token(auth_token)
        print(
            "EDS session: started "
            f"auth={self._redact_token(auth_token)} "
            f"session={self._redact_token(session_token)}"
        )
        return auth_token, session_token


    def end_session(self, auth_token: str, session_token: str):
        """Log out of an EDS session. Should only be done when all work with EDS is done for performance reasons."""
        print(
            "EDS session: ending "
            f"auth={self._redact_token(auth_token)} "
            f"session={self._redact_token(session_token)}"
        )
        result = self._open(self._build_post_request("/edsapi/rest/endsession", {
            "SessionToken": session_token
        }, auth_token))

        try:
            res = json.loads(result)
        except ValueError as exc:
            raise exceptions.EDSQueryError("EDS error: JSON could not be decoded") from exc

        try:
            ended = bool(res["IsSuccessful"])
            print(f"EDS session: ended session token={self._redact_token(session_token)}")
            return ended
        except KeyError as exc:
            self._logger.error("Failed to end session for EDS", exc_info=exc)
            print("EDS session: end failed, see logs")
            # non-fatal error


    def search(self, auth_token: str, session_token: str, phrase: str) -> Generator[Source, Any, None] | list[Any]:
        """Searches for *phrase* in EDS. Returns a bunch of URLs, sorted by relevance (according to EDS).
        """
        print(
            "EDS search: "
            f"phrase_len={len(phrase)} "
            f"auth={self._redact_token(auth_token)} "
            f"session={self._redact_token(session_token)}"
        )
        response = self._open(self._build_post_request("/edsapi/rest/search", {
            "SearchCriteria": {
                "Queries": [{"Term": f"\"{phrase}\""}],
                "Expanders": [ "fulltext" ],
                "Sort": "relevance",
                "IncludeFacets": "n",
                "SearchMode": "all"
            },
            "RetrievalCriteria": {
                "View": "detailed",
                "ResultsPerPage": 10,
                "PageNumber": 1,
                "Highlight": "y"
            }
        }, auth_token, session_token))

        try:
            res = json.loads(response)
        except ValueError as exc:
            raise exceptions.EDSQueryError("EDS error: Search result JSON could not be decoded") from exc

        try:
            hits = res["SearchResult"]["Statistics"]["TotalHits"]
            if hits == 0:
                print("EDS search: no hits")
                return []
            records = res["SearchResult"]["Data"]["Records"]
            print(f"EDS search: records={len(records)}")
            return (Source(
                record["PLink"],
                [
                    (
                        XMLUtils.clean_soup(
                            ["style", "script"],
                            XMLUtils.get_soup(html.unescape(item["Data"])),
                            " "
                        ).rstrip(".")
                        if ("Data" in item and item.get("Name", None) == "Title")
                        else ""
                    )
                    for item in record["Items"]
                ][0]
            ) for record in records)
        except KeyError as exc:
            raise exceptions.EDSQueryError("EDS error: Search result JSON is not in an expected format") from exc

    def get_full_text(self, auth_token: str, session_token: str, db_id: str, an: str) -> OpenedURL | None:
        """
        Gets the full text of a specific EDS resource.
        """
        print(
            "EDS full text: "
            f"db_id={db_id} an={an} "
            f"auth={self._redact_token(auth_token)} "
            f"session={self._redact_token(session_token)}"
        )
        response = self._open(self._build_post_request("/edsapi/rest/retrieve", {
            "DbId": db_id,
            "An": an,
            "HighlightTerms": []
        }, auth_token, session_token))

        try:
            res = json.loads(response)
        except ValueError as exc:
            raise exceptions.EDSQueryError("EDS error: JSON could not be decoded") from exc

        try:
            full_text_info = res["Record"]["FullText"]
            if int(full_text_info["Text"]["Availability"]) == 1:
                # We have fulltext. Spit it out!
                print("EDS full text: inline HTML available")
                return OpenedURL(
                    html.unescape(str(full_text_info["Text"]["Value"])).encode("utf-8"),
                    get_parser("text/xml")
                )
            else:
                # We can try to extract it somehow...
                for link in full_text_info["Links"]:
                    match link["Type"]:
                        case "pdflink":
                            print("EDS full text: PDF link available")
                            return OpenedURL(
                                self._open(Request(link["Url"], headers={
                                    "X-AuthenticationToken": auth_token,
                                    "X-SessionToken": session_token
                                })),
                                get_parser("application/pdf")
                            )
                        case _:
                            self._logger.warning("Unrecognized link type in EDS full text info: %s", link["Type"])
                            print(f"EDS full text: unrecognized link type={link['Type']}")
                # No matching link type found.
                print("EDS full text: no usable links")
                return None
        except KeyError as exc:
            raise exceptions.EDSQueryError("EDS error: Search result JSON is not in an expected format") from exc
