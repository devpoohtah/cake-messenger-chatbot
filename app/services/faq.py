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
        "en": "Yes, you can pick up your cake at our shop on R. Mapa Street, Mandurriao, Iloilo City.",
        "tl": "Opo, puwede po kayong mag-pick up ng cake sa aming shop sa R. Mapa Street, Mandurriao, Iloilo City.",
        "hil": "Huo, puwede ka mag-pick up sang cake sa amon shop sa R. Mapa Street, Mandurriao, Iloilo City.",
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
        "description": "cancelling, changing, or refunding an order",
        "en": "You can cancel an order while it's still pending, which means the owner hasn't started on it yet. Tap My orders to see your orders and cancel one. Once the owner has accepted it, it can't be cancelled here. Tap Talk to the owner if you need help.",
        "tl": "Puwede po ninyong i-cancel ang order habang pending pa ito, ibig sabihin hindi pa ito sinisimulan ng owner. Pindutin ang Mga order ko para makita at ma-cancel ang order ninyo. Kapag tinanggap na ito ng owner, hindi na ito maaaring i-cancel dito. Pindutin ang Kausapin ang owner kung kailangan ninyo ng tulong.",
        "hil": "Puwede mo i-cancel ang order samtang pending pa ini, buot silingon wala pa ini ginsugdan sang owner. Pislita ang Mga order ko agod makita kag ma-cancel ang order mo. Kon natanggap na ini sang owner, indi na ini pwede ma-cancel diri. Pislita ang Kausapon ang owner kon kinahanglan mo sang bulig.",
    },
    "hours": {
        "description": "opening hours of the shop",
        "en": "We're open from 11:00 AM to 10:00 PM.",
        "tl": "Bukas po kami mula 11:00 AM hanggang 10:00 PM.",
        "hil": "Bukas kami halin 11:00 AM tubtob 10:00 PM.",
    },
    "best_seller": {
        "description": "which cake is the best seller, the most popular or recommended cake",
        "en": "We don't have enough orders yet to name a best seller. Tap See menu to look at all our cakes and their prices.",
        "tl": "Wala pa po kaming sapat na order para masabi kung alin ang best seller. Pindutin ang Tingnan ang menu para makita ang lahat ng cake at presyo.",
        "hil": "Wala pa kami sang igo nga order agod masiling kon ano ang best seller. Pislita ang Tan-awa ang menu agod makita ang tanan nga cake kag presyo.",
    },
    "lead_time": {
        "description": "how long until the cake is ready, whether a cake can be had today, or how long delivery takes",
        "en": "Our cakes are premade, so there's no baking wait: pick-up orders are ready as soon as the owner confirms them, during our opening hours. For delivery, the owner will tell you the estimated time when they confirm your order.",
        "tl": "Premade po ang mga cake namin kaya walang hintayan sa pagluluto: handa na ang pick-up order pagkatapos itong kumpirmahin ng owner, sa oras ng bukas namin. Para sa delivery, sasabihin po ng owner ang tinatayang oras kapag kinumpirma ang order ninyo.",
        "hil": "Premade ang mga cake namon kaya wala na sing hulaton sa pagluto: handa na ang pick-up nga order pagkatapos ini ma-confirm sang owner, sa oras nga bukas kami. Para sa delivery, ihambal sang owner ang estimated nga oras kon ma-confirm niya ang order mo.",
    },
    "advance_order": {
        "description": "ordering for a later day or time, reserving or booking a cake in advance, or a pre-order",
        "en": "Sorry, we only take same-day orders. Our cakes are premade, so we can't reserve one for another day. You can order any time while we're open.",
        "tl": "Pasensya na po, same-day orders lang po ang tinatanggap namin. Premade po ang mga cake kaya hindi po kami makakapag-reserve para sa ibang araw. Puwede po kayong mag-order anumang oras habang bukas kami.",
        "hil": "Pasensya na, same-day nga order lang ang ginadawat namon. Premade ang mga cake kaya indi kami makapa-reserve para sa lain nga adlaw. Puwede ka mag-order bisan anong oras samtang bukas kami.",
    },
    "bulk_orders": {
        "description": "ordering many cakes at once, party or event orders, bulk orders, or wholesale",
        "en": "You can order as many cakes as we have in stock. Just choose the cake and the quantity when you order. If you need more than what's available, tap Talk to the owner and we'll see what we can do.",
        "tl": "Puwede po kayong mag-order ng kahit ilang cake basta may stock kami. Piliin lang po ang cake at dami kapag nag-order. Kung kailangan po ninyo ng higit sa available, pindutin ang Kausapin ang owner at titingnan po namin kung ano ang magagawa.",
        "hil": "Puwede ka mag-order sang kadamuon nga cake basta may stock kami. Pilia lang ang cake kag ang kadamuon kon mag-order ka. Kon kinahanglan mo sang labaw sa available, pislita ang Kausapon ang owner kag tan-awon namon kon ano ang mahimo.",
    },
}
