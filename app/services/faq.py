"""Shop FAQ answers. Edit them here. The AI only picks the topic; it never writes these answers.

To add a topic, add an entry with a description (tells the AI which questions
belong to it) and an answer for each language. A topic with no entry here gets
the honest "I don't have that information yet" reply.
"""

FAQS: dict[str, dict[str, str]] = {
    "payment": {
        "description": "payment methods accepted, such as cash or GCash",
        "en": "For now, we accept cash payment only.",
        "tl": "Sa ngayon, cash lang po ang tinatanggap namin.",
        "hil": "Sa subong, cash lang ang gina-accept namon.",
    },
    "hours": {
        "description": "opening hours of the shop",
        "en": "We're open from 11:00 AM to 10:00 PM.",
        "tl": "Bukas po kami mula 11:00 AM hanggang 10:00 PM.",
        "hil": "Bukas kami halin 11:00 AM tubtob 10:00 PM.",
    },
}