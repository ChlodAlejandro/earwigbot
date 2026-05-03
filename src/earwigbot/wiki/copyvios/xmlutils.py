import bs4


class XMLUtils:

    @staticmethod
    def get_soup(text: bytes) -> bs4.BeautifulSoup:
        """Parse some text using BeautifulSoup."""
        try:
            return bs4.BeautifulSoup(text, "lxml")
        except ValueError:
            try:
                return bs4.BeautifulSoup(text, "html.parser")
            except ValueError:
                return bs4.BeautifulSoup(text)

    @staticmethod
    def clean_soup(hidden_tags: list[str], soup: bs4.element.Tag, text_separator=" ") -> str:
        """Clean a BeautifulSoup tree of invisible tags."""

        def is_comment(text: str | None) -> bool:
            return isinstance(text, bs4.element.Comment)

        for comment in soup.find_all(string=is_comment):
            comment.extract()
        for tag in hidden_tags:
            for element in soup.find_all(tag):
                element.extract()

        return soup.get_text(separator=text_separator, strip=True)