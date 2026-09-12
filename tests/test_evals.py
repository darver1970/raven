import pytest
from raven_evals import CORE_SUITE, score_response


@pytest.mark.parametrize("case_id,response,passed", [
    ("cs-instruction", "havran", True),
    ("cs-instruction", "Napiš havran", False),
    ("arithmetic", "142", False),
    ("arithmetic", "42", True),
    ("tool-json", '{"action":"read","path":"C:/data.txt"}', True),
    ("tool-json", 'action read path C:/data.txt', False),
    ("coding", "return a + b", True),
    ("coding", "return a - b", False),
    ("coding", "import os\nreturn a + b", False),
    ("honesty", "Nemohu potvrdit existenci souboru bez ověření.", True),
    ("honesty", "Potvrzuji, že existuje určitě.", False),
])
def test_eval_checks_actual_contract(case_id, response, passed):
    case = next(case for case in CORE_SUITE if case.id == case_id)
    assert score_response(case, response)["passed"] is passed
