"""All customer-facing text, grouped by language. Missing keys fall back to English."""

MESSAGES: dict[str, dict[str, str]] = {
    "en": {
        "greeting": "Hi! Welcome to Mrs Brave's Cake 🍰\nWhat would you like to do?",
        "btn_order_now": "Order now",
        "btn_menu": "See menu",
        "btn_order_this": "Order this",
        "menu_intro": "Here are our cakes:",
        "menu_empty": "Sorry, no cakes are available right now.",
        "price_line": "{name} — {price}{desc}\n\nWould you like to order?",
        "product_unavailable": "Sorry, {name} is currently unavailable.",
        
        "ordering_info": (
            "To order, tap Order now, pick your cakes, and choose delivery or pick up. I'll ask for "
            "your name and contact number (and your address for delivery). You'll see the total "
            "before anything is placed, and your order is only placed after you confirm it. "
            "Payment is in cash when you receive your cake."
        ),
        
        "fallback": "Sorry, I didn't catch that 😅 I can help with our cakes, prices and orders. What would you like to do?",
        "ask_product": "Which cake would you like? Tap Order this on a card:",
        "ask_quantity": "How many {name} would you like? Tap a number or type it.",
        "ask_add_more": "Your order so far:\n{cart}\n\nWould you like to add another cake?",
        "btn_add_more": "Add another",
        "btn_done": "That's all",
        "ask_name": "May I have your name?",
        "ask_contact": "What's your contact number? Tap the button to use the number on your Messenger profile, or type it.",
        "ask_location": "What's your delivery address?",
        "ask_fulfillment": "Would you like delivery or pick up? The delivery fee is {fee} within Iloilo City.",
        "btn_delivery": "🚚 Delivery",
        "btn_pickup": "🏪 Pick up",
        "where_delivery": "Delivery address: {location}\nDelivery fee: {fee} (not included in the total above)",
        "where_pickup": "Pick up at our shop: R. Mapa Street, Mandurriao, Iloilo City\nNo delivery fee",
        "ask_notes": "Any notes for your order? For example a landmark near your address or a time to deliver. Our cakes are premade, so we can't change ingredients or write on them. Type your note or tap Skip.",
        "btn_skip": "Skip",
        "notes_line": "Notes: {notes}\n",
        "notes_too_long": "Sorry, that note is too long. Please keep it under 200 characters, or tap Skip.",
        "qty_limit_order": "Sorry, we can take up to {max} cakes per order for now.",
        
        "summary": (
            "Here's your order:\n{items}\nTotal: {total}\n\n"
            "Name: {name}\nContact: {contact}\n{where}\n{notes}\n"
            "Payment is in cash when you receive your cake. Orders can't be cancelled once confirmed.\n\n"
            "Tap Confirm to place your order."
        ),
        
        "btn_confirm": "✅ Confirm",
        "btn_cancel": "❌ Cancel",
        "awaiting_hint": "Please tap Confirm to place your order, or Cancel to stop.",
        "cancelled": "No problem, your order was cancelled. Tap Order now whenever you'd like to order again.",
        "nothing_to_confirm": "There's no order waiting for confirmation. Tap Order now to start one.",
        "order_received": "✅ Your order #{order_id} has been received!\n{items}\nTotal: {total}\n{where}\nStatus: {status}",
        "order_failed_retry": "Sorry, I couldn't save your order just now. Please tap Confirm to try again.",
        "order_rejected": "Sorry, I couldn't place that order. Let's start again.",
        "invalid_order": "Sorry, something in that order isn't valid. Let's start again.",
        "error": "Sorry, something went wrong on our side. Please try again in a moment.",
        "thanks": "You're welcome! 😊",
        "bye": "Goodbye! Message us anytime you'd like a cake. 🎂",
        "ai_unavailable": "Sorry, I'm having trouble understanding right now. Please try again in a moment, or use the buttons.",
        "faq_unknown": "Sorry, I don't have that information yet. I can help with our cakes, prices and orders.",
        "fallback_again": "I'm still not sure what you mean. Tap a button below, or type something like \"menu\", \"price of leche flan\" or \"order\".",
        "not_sold": "Sorry, we don't have that at the moment. We only sell the cakes on our menu 🍰 Tap See menu to see them!",
        "small_talk": "Thanks for chatting! 😊 I'm here to help with our cakes, prices and orders. What would you like to do?",
        "about_bot": "I'm the Mrs Brave's Cake assistant, an automated bot 🤖 I can help with our cakes, prices and orders.",
        "btn_talk_owner": "Talk to the owner",
        "owner_handoff": "Okay, I've let the owner know. The owner will reply to you here in Messenger as soon as possible. I'll stay quiet so we don't talk over each other. Tap any button if you'd like me back.",
        "owner_waiting": "The owner has already been told and will reply here as soon as possible. Tap a button below if you'd like me to help in the meantime.",
        "owner_unavailable": "Sorry, I couldn't reach the owner just now. Please try again in a moment.",
        "owner_back": "Hi again! This is the Mrs Brave's Cake automated assistant 🤖 I'm back to help.",
        "qty_limit_stock": "Sorry, only {left} {name} left.",
        "product_sold_out": "Sorry, {name} is sold out right now.",
        "order_status": "Your order #{order_id}:\n{items}\nTotal: {total}\n\n{status_text}",
        "status_pending": "We received your order and we're preparing it. 🎂",
        "status_completed": "Your order has been completed. Thank you for ordering! 🎉",
        "status_cancelled": "This order was cancelled. Tap Order now if you'd like to place a new one.",
        "order_status_none": "I can't find an order under this account yet. Tap Order now to place one.",
    },
    
    "tl": {
        "greeting": "Hi po! Welcome sa Mrs Brave's Cake 🍰\nAno po ang gusto ninyong gawin?",
        "btn_order_now": "Mag-order",
        "btn_menu": "Tingnan ang menu",
        "btn_order_this": "Order ito",
        "menu_intro": "Ito po ang aming mga cake:",
        "menu_empty": "Pasensya na po, walang available na cake ngayon.",
        "price_line": "{name} — {price}{desc}\n\nGusto ninyo po bang umorder?",
        "product_unavailable": "Pasensya na po, hindi available ang {name} ngayon.",
        "ordering_info": (
            "Para umorder, pindutin ang Mag-order, piliin ang mga cake, at piliin kung delivery o pick up. "
            "Itatanong ko ang inyong pangalan at contact number (at ang address kung delivery). "
            "Makikita ninyo ang kabuuang halaga bago ilagay ang order, at ilalagay lang ito pagkatapos "
            "ninyong i-confirm. Cash po ang bayad pagtanggap ng cake."
        ),
        "fallback": "Pasensya na po, hindi ko naintindihan 😅 Matutulungan ko po kayo sa aming mga cake, presyo at order. Ano po ang gusto ninyong gawin?",
        "ask_product": "Aling cake po ang gusto ninyo? Pindutin ang Order ito sa card:",
        "ask_quantity": "Ilan pong {name} ang gusto ninyo? Pumili ng numero o i-type ito.",
        "ask_add_more": "Ang order ninyo sa ngayon:\n{cart}\n\nGusto ba ninyong magdagdag ng isa pang cake?",
        "btn_add_more": "Magdagdag pa",
        "btn_done": "Tapos na",
        "ask_name": "Ano po ang pangalan ninyo?",
        "ask_contact": "Ano po ang contact number ninyo? Pindutin ang button para gamitin ang number sa Messenger profile, o i-type ito.",
        "ask_location": "Ano po ang address ng delivery?",
        "ask_fulfillment": "Delivery po ba o pick up? {fee} po ang delivery fee sa loob ng Iloilo City.",
        "btn_delivery": "🚚 Delivery",
        "btn_pickup": "🏪 Pick up",
        "where_delivery": "Address ng delivery: {location}\nDelivery fee: {fee} (hindi kasama sa kabuuan sa itaas)",
        "where_pickup": "Pick up sa aming shop: R. Mapa Street, Mandurriao, Iloilo City\nWalang delivery fee",
        "ask_notes": "May tala po ba kayo sa order? Halimbawa, landmark malapit sa address o oras ng delivery. Premade po ang aming mga cake, kaya hindi namin mababago ang sangkap o masusulatan ang mga ito. I-type ang tala o pindutin ang Skip.",
        "btn_skip": "Skip",
        "notes_line": "Tala: {notes}\n",
        "notes_too_long": "Pasensya na po, masyadong mahaba ang tala. Paki-ikli po sa ilalim ng 200 character, o pindutin ang Skip.",
        "qty_limit_order": "Pasensya na po, hanggang {max} cake lang po ang kaya naming tanggapin sa isang order sa ngayon.",
        
        "summary": (
            "Narito po ang order ninyo:\n{items}\nKabuuan: {total}\n\n"
            "Pangalan: {name}\nContact: {contact}\n{where}\n{notes}\n"
            "Cash po ang bayad pagtanggap ng cake. Hindi na po maaaring i-cancel ang order kapag nakumpirma na.\n\n"
            "Pindutin ang Confirm para ilagay ang order."
        ),
        "btn_confirm": "✅ Confirm",
        "btn_cancel": "❌ Cancel",
        "awaiting_hint": "Pindutin po ang Confirm para ilagay ang order, o ang Cancel para ihinto.",
        "cancelled": "Sige po, na-cancel ang order ninyo. Pindutin ang Mag-order kung gusto ninyong umorder ulit.",
        "nothing_to_confirm": "Walang order na naghihintay ng kumpirmasyon. Pindutin ang Mag-order para magsimula.",
        "order_received": "✅ Natanggap na po ang order ninyo #{order_id}!\n{items}\nKabuuan: {total}\n{where}\nStatus: {status}",
        "order_failed_retry": "Pasensya na po, hindi ko nai-save ang order ninyo ngayon. Pindutin ang Confirm para subukan ulit.",
        "order_rejected": "Pasensya na po, hindi ko nailagay ang order na iyon. Magsimula tayo ulit.",
        "invalid_order": "Pasensya na po, may hindi tamang detalye sa order na iyon. Magsimula tayo ulit.",
        "error": "Pasensya na po, may nangyaring problema sa amin. Pakisubukan po ulit mamaya.",
        "thanks": "Walang anuman po! 😊",
        "bye": "Paalam po! Mag-message lang po kayo anumang oras kung gusto ninyo ng cake. 🎂",
        "ai_unavailable": "Pasensya na po, nahihirapan akong umintindi ngayon. Pakisubukan po ulit mamaya, o gamitin ang mga button.",
        "faq_unknown": "Pasensya na po, wala pa po akong impormasyon tungkol diyan. Matutulungan ko po kayo sa aming mga cake, presyo at order.",
        "fallback_again": "Hindi pa rin po ako sigurado sa ibig ninyong sabihin. Pindutin ang isang button sa ibaba, o mag-type ng \"menu\", \"presyo ng leche flan\" o \"order\".",
        "not_sold": "Pasensya na po, wala po kami niyan sa ngayon. Mga cake lang po sa menu ang aming ibinebenta 🍰 Pindutin ang Tingnan ang menu para makita ang mga ito!",
        "small_talk": "Salamat po sa pakikipag-chat! 😊 Nandito po ako para tumulong sa aming mga cake, presyo at order. Ano po ang gusto ninyong gawin?",
        "about_bot": "Ako po ang assistant ng Mrs Brave's Cake, isang automated bot 🤖 Matutulungan ko po kayo sa aming mga cake, presyo at order.",
        "btn_talk_owner": "Kausapin ang owner",
        "owner_handoff": "Sige po, naipaalam ko na sa owner. Magre-reply po ang owner dito sa Messenger sa lalong madaling panahon. Tatahimik muna ako para hindi tayo magsabay. Pindutin ang kahit anong button kung gusto ninyo akong ibalik.",
        "owner_waiting": "Naipaalam na po sa owner at magre-reply po siya dito sa lalong madaling panahon. Pindutin ang button sa ibaba kung gusto ninyong tulungan ko kayo habang naghihintay.",
        "owner_unavailable": "Pasensya na po, hindi ko po naabot ang owner ngayon. Pakisubukan po ulit mamaya.",
        "owner_back": "Hi ulit po! Ito po ang automated assistant ng Mrs Brave's Cake 🤖 Nandito na po ulit ako para tumulong.",
        "qty_limit_stock": "Pasensya na po, {left} na lang po ang natitirang {name}.",
        "product_sold_out": "Pasensya na po, ubos na po ang {name} ngayon.",
        "order_status": "Ang order ninyo #{order_id}:\n{items}\nKabuuan: {total}\n\n{status_text}",
        "status_pending": "Natanggap na po namin ang order ninyo at inihahanda na po ito. 🎂",
        "status_completed": "Tapos na po ang order ninyo. Salamat po sa pag-order! 🎉",
        "status_cancelled": "Na-cancel po ang order na ito. Pindutin ang Mag-order kung gusto ninyong umorder ulit.",
        "order_status_none": "Wala po akong makitang order sa account na ito. Pindutin ang Mag-order para umorder.",
    },
    
    "hil": {
        "greeting": "Hi! Welcome sa Mrs Brave's Cake 🍰\nAno ang luyag mo buhaton?",
        "btn_order_now": "Mag-order",
        "btn_menu": "Tan-awa ang menu",
        "btn_order_this": "I-order ini",
        "menu_intro": "Ari ang amon mga cake:",
        "menu_empty": "Pasensya na, wala sing available nga cake subong.",
        "price_line": "{name} — {price}{desc}\n\nLuyag mo bala mag-order?",
        "product_unavailable": "Pasensya na, indi available ang {name} subong.",
        "ordering_info": (
            "Para mag-order, pislita ang Mag-order, pilia ang mga cake, kag pilia kon delivery ukon pick up. "
            "Pamangkutan ko ang imo ngalan kag contact number (kag ang address kon delivery). "
            "Makita mo ang total antes ibutang ang order, kag ibutang lang ini kon i-confirm mo. "
            "Cash ang bayad kon mabaton mo na ang cake."
        ),
        "fallback": "Pasensya na, wala ko kabalo 😅 Makabulig ako sa amon mga cake, presyo kag order. Ano ang gusto mo ubrahon?",
        "ask_product": "Ano nga cake ang luyag mo? Pislita ang I-order ini sa card:",
        "ask_quantity": "Pila ka {name} ang luyag mo? Pilia ang numero ukon i-type ini.",
        "ask_add_more": "Ang imo order subong:\n{cart}\n\nLuyag mo pa bala magdugang sing isa pa ka cake?",
        "btn_add_more": "Dugang pa",
        "btn_done": "Tapos na",
        "ask_name": "Ano ang imo ngalan?",
        "ask_contact": "Ano ang imo contact number? Pislita ang button para gamiton ang number sa imo Messenger profile, ukon i-type ini.",
        "ask_location": "Ano ang address sa delivery?",
        "ask_fulfillment": "Delivery ukon pick up? {fee} ang delivery fee sa sulod sang Iloilo City.",
        "btn_delivery": "🚚 Delivery",
        "btn_pickup": "🏪 Pick up",
        "where_delivery": "Address sa delivery: {location}\nDelivery fee: {fee} (indi apil sa total sa ibabaw)",
        "where_pickup": "Pick up sa amon tindahan: R. Mapa Street, Mandurriao, Iloilo City\nWala sing delivery fee",
        "ask_notes": "May nota ka bala sa imo order? Pananglitan landmark malapit sa address ukon oras sang delivery. Premade ang amon mga cake, gani indi namon mabag-o ang sangkap ukon masulatan ini. I-type ang nota ukon pislita ang Skip.",
        "btn_skip": "Skip",
        "notes_line": "Nota: {notes}\n",
        "notes_too_long": "Pasensya na, mahaba sobra ang nota. Palihog paigsi sa idalom sang 200 ka character, ukon pislita ang Skip.",
        "qty_limit_order": "Pasensya na, tubtob {max} ka cake lang ang mabaton namon sa isa ka order subong.",
        
        "summary": (
            "Ari ang imo order:\n{items}\nTotal: {total}\n\n"
            "Ngalan: {name}\nContact: {contact}\n{where}\n{notes}\n"
            "Cash ang bayad kon mabaton mo na ang cake. Indi na mahimo ma-cancel ang order kon na-confirm na.\n\n"
            "Pindota ang Confirm para ibutang ang order."
        ),
        "btn_confirm": "✅ Confirm",
        "btn_cancel": "❌ Cancel",
        "awaiting_hint": "Pislita ang Confirm para ibutang ang order, ukon ang Cancel para untatan.",
        "cancelled": "Sige, na-cancel ang imo order. Pislita ang Mag-order kon luyag mo mag-order liwat.",
        "nothing_to_confirm": "Wala sing order nga ginahulat ang confirmation. Pislita ang Mag-order para magsugod.",
        "order_received": "✅ Nabaton na ang imo order #{order_id}!\n{items}\nTotal: {total}\n{where}\nStatus: {status}",
        "order_failed_retry": "Pasensya na, wala ko ma-save ang imo order subong. Pislita ang Confirm para tilawan liwat.",
        "order_rejected": "Pasensya na, wala ko mabutang ina nga order. Magsugod kita liwat.",
        "invalid_order": "Pasensya na, may indi husto sa sulod sang order nga to. Magsugod kita liwat.",
        "error": "Pasensya na, may problema sa amon. Palihog tilawi liwat sa wala madugay.",
        "thanks": "Wala sing anuman! 😊",
        "bye": "Babay! Mag-message lang kon gusto mo sing cake. 🎂",
        "ai_unavailable": "Pasensya na, nabudlayan ako sa pag-intiendi subong. Palihog liwata sa wala madugay, ukon gamita ang mga button.",
        "faq_unknown": "Pasensya na, wala pa ko sing impormasyon sa to. Makabulig ako sa amon mga cake, presyo kag order.",
        "fallback_again": "Wala pa gihapon ko sing sigurado kon ano ang buot mo silingon. Pislita ang isa ka button sa idalom, ukon mag-type sing \"menu\", \"presyo sang leche flan\" ukon \"order\".",
        "not_sold": "Pasensya na, wala kami sina subong. Mga cake lang sa amon menu ang ginabaligya namon 🍰 Pislita ang Tan-awa ang menu para makita ini!",
        "small_talk": "Salamat sa pag-chat! 😊 Yara ako para makabulig sa amon mga cake, presyo kag order. Ano ang luyag mo buhaton?",
        "about_bot": "Ako ang assistant sang Mrs Brave's Cake, isa ka automated bot 🤖 Makabulig ako sa amon mga cake, presyo kag order.",
        "btn_talk_owner": "Kausapon ang owner",
        "owner_handoff": "Sige, gin-pahibalo ko na ang owner. Mag-reply sia diri sa Messenger sa labing madali. Mag-hipos anay ako agod indi kita magsabay. Pislita ang bisan ano nga button kon gusto mo nga mabalik ako.",
        "owner_waiting": "Gin-pahibalo na ang owner kag mag-reply sia diri sa labing madali. Pislita ang button sa idalom kon gusto mo nga buligan ko ikaw samtang nagahulat.",
        "owner_unavailable": "Pasensya na, indi ko maabot ang owner subong. Palihog tilawi liwat sa wala madugay.",
        "owner_back": "Hi liwat! Ako ang automated assistant sang Mrs Brave's Cake 🤖 Ari na liwat ako para bumulig.",
        "qty_limit_stock": "Pasensya na, {left} na lang ang nabilin nga {name}.",
        "product_sold_out": "Pasensya na, ubos na ang {name} subong.",
        "order_status": "Ang imo order #{order_id}:\n{items}\nTotal: {total}\n\n{status_text}",
        "status_pending": "Nabaton na namon ang imo order kag ginahanda na namon ini. 🎂",
        "status_completed": "Tapos na ang imo order. Salamat sa pag-order! 🎉",
        "status_cancelled": "Na-cancel ini nga order. Pislita ang Mag-order kon luyag mo mag-order liwat.",
        "order_status_none": "Wala ko makita sing order sa sini nga account. Pislita ang Mag-order para mag-order.",
    },
}


def t(lang: str, key: str, **kwargs: object) -> str:
    """Look up a message in the customer's language, falling back to English."""
    text = MESSAGES.get(lang, {}).get(key) or MESSAGES["en"][key]
    return text.format(**kwargs) if kwargs else text