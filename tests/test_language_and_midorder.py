"""A name or address must never switch the reply language, and several cakes typed in the middle of an order are
repeated back for a Yes instead of being ignored."""
from app.services.ai_service import AIResult, Intent, QuoteItem
from app.services.conversation_service import COLLECTING, _pick_language
from app.services.messages import t
from tests.test_ordering_fixes import ScriptedAI, db, draft_of, order, payloads, say, shop_open  # noqa: F401 (autouse fixture)


def _asking_for_name(database):
    """Walk an English customer to the 'May I have your name?' question."""
    say(database, ScriptedAI(order(("Nutella Ferrero Cake", 3), lang="en")), "I want 3 nutella")
    say(database, ScriptedAI(), payload="DONE_ADDING")
    reply = say(database, ScriptedAI(), payload="FULFILL:delivery")[0]
    assert reply.text == t("en", "ask_name")
    assert draft_of(database)["asking"] == "customer_name"


# ---------- language ----------
def test_a_nickname_the_ai_guesses_as_tagalog_does_not_switch_the_language():
    database = db()
    _asking_for_name(database)
    guess = AIResult(intent=Intent.PROVIDE_ORDER_DETAILS, language="tl", customer_name="krizzyy")  # the bug in the log
    reply = say(database, ScriptedAI(guess), "krizzyy")[0]
    assert draft_of(database)["lang"] == "en"
    assert reply.text == t("en", "ask_contact")


def test_the_ai_saying_unknown_for_a_name_also_keeps_the_language():
    database = db()
    _asking_for_name(database)
    reply = say(database, ScriptedAI(AIResult(intent=Intent.UNKNOWN, language="hil")), "krizzyy")[0]
    assert draft_of(database)["lang"] == "en" and reply.text == t("en", "ask_contact")


def test_clear_tagalog_words_in_a_name_answer_still_switch_the_language():
    database = db()
    _asking_for_name(database)
    say(database, ScriptedAI(AIResult(intent=Intent.PROVIDE_ORDER_DETAILS, language="tl", customer_name="Ana")), "Ana po")
    assert draft_of(database)["lang"] == "tl"


def test_a_real_question_while_asking_the_name_still_follows_the_ai_language():
    database = db()
    _asking_for_name(database)
    say(database, ScriptedAI(AIResult(intent=Intent.FAQ_QUESTION, language="tl")), "magkano ang delivery?")
    assert draft_of(database)["lang"] == "tl"


def test_pick_language_strict_ignores_the_ai_and_uses_clue_words_only():
    assert _pick_language("tl", "krizzyy", strict=True) is None
    assert _pick_language("hil", "lapaz plaza", strict=True) is None
    assert _pick_language("en", "Ana po", strict=True) == "tl"
    assert _pick_language("tl", "magkano po ang yema?") == "tl"  # normal mode is unchanged


# ---------- several cakes in the middle of an order ----------
def _mid_order(database):
    say(database, ScriptedAI(order(("Nutella Ferrero Cake", 3))), "I want 3 nutella")
    assert draft_of(database)["asking"] == "add_more"


def _typed_list():
    return AIResult(intent=Intent.PROVIDE_ORDER_DETAILS, language="en", items=[
        QuoteItem(product="Leche Flan", quantity=1), QuoteItem(product="Yema Cake", quantity=2)])


def test_several_cakes_typed_mid_order_are_repeated_back_and_not_added_yet():
    database = db()
    _mid_order(database)
    reply = say(database, ScriptedAI(_typed_list()), "1 leche flan and 2 yema")[0]
    assert payloads(reply) == ["ITEMS_YES", "ITEMS_NO"]
    assert "1 × Leche Flan" in reply.text and "2 × Yema Cake" in reply.text
    row = database.tables["conversations"][0]
    assert row["state"] == COLLECTING and row["draft"]["cart"] == [{"product": "Nutella Ferrero Cake", "quantity": 3}]


def test_yes_adds_them_to_the_existing_cart_and_moves_on():
    database = db()
    _mid_order(database)
    say(database, ScriptedAI(_typed_list()), "1 leche flan and 2 yema")
    reply = say(database, ScriptedAI(), payload="ITEMS_YES")[0]
    cart = {i["product"]: i["quantity"] for i in draft_of(database)["cart"]}
    assert cart == {"Nutella Ferrero Cake": 3, "Leche Flan": 1, "Yema Cake": 2}
    assert payloads(reply) == ["FULFILL:delivery", "FULFILL:pickup"]


def test_no_mid_order_adds_nothing_and_asks_the_same_question_again():
    database = db()
    _mid_order(database)
    say(database, ScriptedAI(_typed_list()), "1 leche flan and 2 yema")
    reply = say(database, ScriptedAI(), payload="ITEMS_NO")[0]
    assert reply.text.startswith(t("en", "items_no_mid"))
    assert payloads(reply) == ["ADD_MORE", "DONE_ADDING"]
    assert draft_of(database)["cart"] == [{"product": "Nutella Ferrero Cake", "quantity": 3}]
