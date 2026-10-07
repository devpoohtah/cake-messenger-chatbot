"""Shop FAQ answers. Edit them here. The AI only picks the topic; it never writes these answers.

To add a topic, add an entry with a description (tells the AI which questions
belong to it) and an answer for each language. A topic with no entry here gets
the honest "I don't have that information yet" reply.
"""

FAQS: dict[str, dict[str, str]] = {
    "payment": {
        "description": "which payment methods are accepted, such as cash or GCash, and when payment is collected",
        "en": "We accept cash only for now. You pay when you receive your cake, on delivery or at pickup.",
        "tl": "Cash lang po muna ang tinatanggap namin. Magbabayad po kayo pagtanggap ng cake, sa delivery man o sa pick up.",
        "hil": "Cash lang anay ang gina-accept namon. Magbayad ka kon mabaton mo na ang cake, sa delivery ukon sa pick up.",
    },
    "credit": {
        "description": "asking to pay later, on credit (utang), by installment, or to pay a downpayment or deposit",
        "en": "Sorry, we don't offer credit or installments, and no downpayment is needed. You pay in cash when you receive your cake.",
        "tl": "Pasensya na po, wala po kaming utang o hulugan, at hindi rin po kailangan ng downpayment. Cash po ang bayad pagtanggap ng cake.",
        "hil": "Pasensya na, wala kami sing utang ukon hulugan, kag indi man kinahanglan ang downpayment. Cash ang bayad kon mabaton mo na ang cake.",
    },
    "delivery": {
        "description": "whether the shop delivers, which areas it delivers to, or how much the delivery fee is",
        "en": "Yes, we deliver within Iloilo City. The delivery fee is ₱50. Just give us your delivery address when you order.",
        "tl": "Opo, nagde-deliver po kami sa loob ng Iloilo City. ₱50 po ang delivery fee. Ibigay lang po ang address ng delivery kapag umorder kayo.",
        "hil": "Huo, nagadeliver kami sa sulod sang Iloilo City. ₱50 ang delivery fee. Ihatag lang ang address sa delivery kon mag-order ka.",
    },
    "pickup": {
        "description": "whether customers can pick up their order at the shop instead of having it delivered",
        "en": "Yes, you can pick up your cake at our shop on R. Mapa Street, Mandurriao, Iloilo City. We're open 11:00 AM to 10:00 PM.",
        "tl": "Opo, puwede po kayong mag-pick up ng cake sa aming shop sa R. Mapa Street, Mandurriao, Iloilo City. Bukas po kami mula 11:00 AM hanggang 10:00 PM.",
        "hil": "Huo, puwede ka mag-pick up sang cake sa amon shop sa R. Mapa Street, Mandurriao, Iloilo City. Bukas kami halin 11:00 AM tubtob 10:00 PM.",
    },
    "location": {
        "description": "where the shop is located, its address, or how to find the shop",
        "en": "Our shop is at R. Mapa Street, Mandurriao, Iloilo City.",
        "tl": "Nasa R. Mapa Street, Mandurriao, Iloilo City po ang aming shop.",
        "hil": "Ang amon shop yara sa R. Mapa Street, Mandurriao, Iloilo City.",
    },
    "custom_cakes": {
        "description": "custom designed cakes, special requests, or cakes that are not on the menu",
        "en": "Sorry, we only offer the cakes on our menu for now. Tap See menu to view them.",
        "tl": "Pasensya na po, ang mga cake lang po sa menu ang available ngayon. Pindutin ang Tingnan ang menu para makita ang mga ito.",
        "hil": "Pasensya na, ang mga cake lang sa menu ang available subong. Tum-oka ang Tan-awa ang menu para makita ini.",
    },
    "cancellation": {
        "description": "cancelling, changing, or refunding an order after it was confirmed",
        "en": "Sorry, orders can't be cancelled once they're confirmed. You can cancel any time before you tap Confirm.",
        "tl": "Pasensya na po, hindi na po maaaring i-cancel ang order kapag nakumpirma na. Maaari po kayong mag-cancel anumang oras bago pindutin ang Confirm.",
        "hil": "Pasensya na, indi na pwede ma-cancel ang order kon na-confirm na. Puwede ka mag-cancel antes mo pindoton ang Confirm.",
    },
    "hours": {
        "description": "opening hours of the shop",
        "en": "We're open from 11:00 AM to 10:00 PM.",
        "tl": "Bukas po kami mula 11:00 AM hanggang 10:00 PM.",
        "hil": "Bukas kami halin 11:00 AM tubtob 10:00 PM.",
    },
}