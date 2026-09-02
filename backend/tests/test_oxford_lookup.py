from fastapi.testclient import TestClient
from urllib.error import HTTPError

from app.lookup import lookup_oxford_word, parse_oxford_lookup_html
from app.main import create_app


OXFORD_HTML = """
<div id="entryContent" class="oald">
  <div class="entry">
    <h1 class="headword">atmosphere</h1>
    <span class="pos">noun</span>
    <ol class="senses_multiple">
      <li class="sense">
        <span class="grammar">[singular]</span>
        <span class="def">the mixture of gases that surrounds the earth</span>
        <ul class="examples">
          <li><span class="x">Wind power doesn't release carbon dioxide into the atmosphere.</span></li>
        </ul>
      </li>
      <li class="sense">
        <span class="grammar">[countable]</span>
        <span class="def">a mixture of gases that surrounds another planet or a star</span>
        <ul class="examples">
          <li><span class="x">The probe will plunge into the planet's stormy atmosphere.</span></li>
        </ul>
      </li>
    </ol>
  </div>
</div>
"""

GAS_HTML = """
<div id="entryContent" class="oald">
  <div class="entry">
    <h1 class="headword">gas</h1>
    <span class="pos">noun</span>
    <ol class="senses_multiple">
      <li class="sense">
        <span class="def">any substance like air that is neither a solid nor a liquid</span>
      </li>
    </ol>
  </div>
</div>
"""

JEOPARDIZE_HTML = """
<div id="entryContent" class="oald">
  <div class="entry">
    <h1 class="headword">jeopardize</h1>
    <span class="pos">verb</span>
    <ol class="senses_multiple">
      <li class="sense">
        <span class="def">to risk harming or destroying something/somebody</span>
        <ul class="examples">
          <li><span class="x">He would never do anything to jeopardize his career.</span></li>
        </ul>
      </li>
    </ol>
  </div>
</div>
"""


def test_parse_oxford_lookup_html_extracts_definitions_and_examples():
    result = parse_oxford_lookup_html("atmosphere", OXFORD_HTML)

    assert result.word == "atmosphere"
    assert result.sourceUrl == "https://www.oxfordlearnersdictionaries.com/definition/english/atmosphere?q=atmosphere"
    assert result.senses[0].partOfSpeech == "noun"
    assert result.senses[0].definition == "the mixture of gases that surrounds the earth"
    assert result.senses[0].example == "Wind power doesn't release carbon dioxide into the atmosphere."
    assert result.senses[1].definition == "a mixture of gases that surrounds another planet or a star"


def test_parse_oxford_lookup_html_ignores_idiom_definitions():
    html = (
        OXFORD_HTML
        + """
        <div class="idioms">
          <li class="sense">
            <span class="def">a situation when people do not say anything, but feel embarrassed</span>
          </li>
        </div>
        """
    )

    result = parse_oxford_lookup_html("atmosphere", html)

    assert [sense.definition for sense in result.senses] == [
        "the mixture of gases that surrounds the earth",
        "a mixture of gases that surrounds another planet or a star",
    ]


def test_lookup_route_returns_oxford_senses(monkeypatch):
    def fake_fetch(word: str):
        return parse_oxford_lookup_html(word, OXFORD_HTML)

    monkeypatch.setattr("app.routes.lookup_oxford_word", fake_fetch)
    client = TestClient(create_app())

    response = client.get("/api/lookup/oxford?word=atmosphere")

    assert response.status_code == 200
    assert response.json()["senses"][0]["definition"] == "the mixture of gases that surrounds the earth"


def test_lookup_oxford_word_falls_back_from_plural_to_base_form(monkeypatch):
    requested_urls: list[str] = []

    class FakeResponse:
        def __init__(self, html: str):
            self.html = html

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.html.encode("utf-8")

    def fake_urlopen(request, timeout: int):
        requested_urls.append(request.full_url)
        if "/gases?" in request.full_url:
            return FakeResponse("<div id='entryContent'></div>")
        if "/gas?" in request.full_url:
            return FakeResponse(GAS_HTML)
        raise AssertionError(f"unexpected URL: {request.full_url}")

    monkeypatch.setattr("app.lookup.urlopen", fake_urlopen)

    result = lookup_oxford_word("gases")

    assert result.word == "gas"
    assert result.senses[0].definition == "any substance like air that is neither a solid nor a liquid"
    assert requested_urls == [
        "https://www.oxfordlearnersdictionaries.com/definition/english/gases?q=gases",
        "https://www.oxfordlearnersdictionaries.com/definition/english/gas?q=gas",
    ]


def test_lookup_oxford_word_continues_to_base_form_after_http_error(monkeypatch):
    class FakeResponse:
        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return GAS_HTML.encode("utf-8")

    def fake_urlopen(request, timeout: int):
        if "/gases?" in request.full_url:
            raise HTTPError(request.full_url, 404, "Not Found", {}, None)
        if "/gas?" in request.full_url:
            return FakeResponse()
        raise AssertionError(f"unexpected URL: {request.full_url}")

    monkeypatch.setattr("app.lookup.urlopen", fake_urlopen)

    result = lookup_oxford_word("gases")

    assert result.word == "gas"
    assert result.senses[0].definition == "any substance like air that is neither a solid nor a liquid"


def test_lookup_oxford_word_falls_back_from_british_ise_to_american_ize(monkeypatch):
    requested_urls: list[str] = []

    class FakeResponse:
        def __init__(self, html: str):
            self.html = html

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

        def read(self):
            return self.html.encode("utf-8")

    def fake_urlopen(request, timeout: int):
        requested_urls.append(request.full_url)
        if "/jeopardise?" in request.full_url:
            return FakeResponse("<div id='entryContent'></div>")
        if "/jeopardize?" in request.full_url:
            return FakeResponse(JEOPARDIZE_HTML)
        raise AssertionError(f"unexpected URL: {request.full_url}")

    monkeypatch.setattr("app.lookup.urlopen", fake_urlopen)

    result = lookup_oxford_word("jeopardise")

    assert result.word == "jeopardize"
    assert result.senses[0].partOfSpeech == "verb"
    assert result.senses[0].definition == "to risk harming or destroying something/somebody"
    assert result.senses[0].example == "He would never do anything to jeopardize his career."
    assert requested_urls == [
        "https://www.oxfordlearnersdictionaries.com/definition/english/jeopardise?q=jeopardise",
        "https://www.oxfordlearnersdictionaries.com/definition/english/jeopardize?q=jeopardize",
    ]
