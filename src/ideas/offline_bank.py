"""Hand-written skits used when no text LLM is reachable, so a run never dies at the idea stage."""
from __future__ import annotations

S, M = "ar-EG-SalmaNeural", "ar-EG-ShakirNeural"

LOOKS = {
    "tomato": ("طماطم", M, "a plump shiny red tomato with a green leafy cap, big expressive eyes, thick eyebrows"),
    "onion": ("بصلة", S, "a round golden-brown onion with a little sprout on top, long eyelashes, rosy cheeks"),
    "potato": ("بطاطس", M, "a chubby brown potato with small spots, round friendly eyes, a cheeky grin"),
    "cucumber": ("خيارة", S, "a long fresh green cucumber with tiny bumps, bright eyes and a tiny flower on her head"),
    "banana": ("موزة", S, "a curved yellow banana with a few brown freckles, big sad eyes, a little bow"),
    "watermelon": ("بطيخة", M, "a big round striped green watermelon, cheerful wide eyes, a kind smile"),
    "lemon": ("ليمونة", S, "a bright yellow lemon with a green leaf on top, confident eyes, sassy expression"),
    "eggplant": ("بتنجان", M, "a glossy purple eggplant with a green cap like a hat, proud eyes, a mustache"),
}

KITCHEN = "on a sunny Egyptian kitchen counter"


def _c(cid: str) -> dict:
    name, voice, look = LOOKS[cid]
    return {"id": cid, "name": name, "voice": voice, "look": look}


def _s(cid: str, line: str, emotion: str, action: str) -> dict:
    return {
        "character_id": cid,
        "line_ar": line,
        "visual_prompt_en": f"{LOOKS[cid][2]} {action} {KITCHEN}",
        "emotion": emotion,
    }


SKITS = [
    {
        "title": "الطماطم والبصلة مين أهم في الطبخة",
        "format": "rivalry",
        "characters": [_c("tomato"), _c("onion")],
        "scenes": [
            _s("tomato", "بصي يا بصلة، من غيري مفيش ولا طبخة في مصر كلها تنفع، أنا أساس الصلصة يا حبيبتي!", "excited", "talking proudly, pointing at itself"),
            _s("onion", "أساس إيه يا طماطم؟ ده أنا أول حاجة تنزل الحلة، ومن غيري الأكل ملوش طعم خالص.", "sarcastic", "rolling her eyes, arms crossed"),
            _s("tomato", "طب وإنتي بتعملي إيه غير إنك بتعيطي الناس وهي بتقطعك؟", "angry", "frowning and waving its leaves"),
            _s("onion", "دي دموع فرح يا حبيبي، الناس بتعيط من جمالي، مش زيك بتبوظ في التلاجة بعد يومين.", "happy", "laughing and flipping her sprout"),
            _s("tomato", "خلاص خلاص، إيه رأيك نتصالح ونعمل مع بعض أحلى طاجن بامية؟", "neutral", "smiling and offering a handshake"),
            _s("onion", "ماشي، بس اعرف إن البامية من غير التقلية بتاعتي ولا حاجة! تابعونا عشان تعرفوا مين كسب.", "excited", "winking at the camera"),
        ],
        "caption_ar": "خناقة كل مطبخ مصري 😂 إنتو مع الطماطم ولا البصلة؟",
        "hashtags": ["خضار_بيتكلم", "مطبخ_مصري", "كوميدي", "fyp"],
    },
    {
        "title": "اعتراف البطاطس المقلية",
        "format": "advice",
        "characters": [_c("potato"), _c("cucumber")],
        "scenes": [
            _s("potato", "يا جماعة أنا البطاطس، وعندي اعتراف مهم لازم تسمعوه النهارده.", "neutral", "standing seriously, looking at the camera"),
            _s("potato", "كل يوم تقلوني في الزيت وتقولوا ده آخر مرة، وبكرة ترجعوا تاني!", "sad", "sweating and shaking its head"),
            _s("cucumber", "معاه حق والله، جربوا تاكلوه مسلوق أو في الفرن، هيبقى طعمه حلو وصحتكم أحسن.", "happy", "nodding wisely"),
            _s("potato", "شكرا يا خيارة، إنتي دايما فاهماني، مش زي الكاتشب اللي بيغطي على طعمي.", "happy", "hugging itself happily"),
            _s("cucumber", "وكمان اشربوا مية كتير، أنا تسعين في المية مية، عشان كده بشرتي حلوة كده.", "excited", "posing like a model"),
            _s("potato", "اكتبولنا في الكومنتات، بتحبوني مقلي ولا في الفرن؟ ومتنسوش تعملوا متابعة!", "excited", "waving at the camera"),
        ],
        "caption_ar": "البطاطس زهقت من الزيت 🥔 إنتو بتحبوها إزاي؟",
        "hashtags": ["خضار_بيتكلم", "نصيحة", "أكل_صحي", "fyp"],
    },
    {
        "title": "الموزة اللي خايفة يرموها",
        "format": "drama",
        "characters": [_c("banana"), _c("watermelon")],
        "scenes": [
            _s("banana", "محدش بيسأل فيا غير لما أبقى صفرا ومظبوطة، وأول ما أسمر شوية بيرموني.", "sad", "sitting alone, looking down sadly"),
            _s("watermelon", "مالك يا موزة؟ وشك أسمر ليه كده النهارده، إنتي زعلانة؟", "surprised", "rolling closer with a worried face"),
            _s("banana", "آه يا بطيخ، سمعت ماما بتقول هنرميها بكرة عشان خلاص استوت زيادة.", "sad", "crying small tears"),
            _s("watermelon", "لا يا شيخة! ده إنتي كده أحلى، الموزة المستوية بتعمل أحلى كيكة وأحلى ميلك شيك.", "excited", "smiling widely and encouraging"),
            _s("banana", "بجد؟ يعني أنا لسه ليا لازمة ومش هيرموني؟", "surprised", "wiping tears, starting to smile"),
            _s("watermelon", "طبعا، يا جماعة متكبوش الموز الأسمر، اعملوه عصير أو كيك وابعتولنا الصورة!", "happy", "giving a thumbs up to the camera"),
        ],
        "caption_ar": "متكبوش الموز الأسمر 🍌💔 اعملوه كيكة!",
        "hashtags": ["فواكه_بتتكلم", "دراما", "مطبخ_مصري", "fyp"],
    },
    {
        "title": "الليمونة ملكة السفرة",
        "format": "comedy",
        "characters": [_c("lemon"), _c("eggplant")],
        "scenes": [
            _s("lemon", "أنا الليمونة، ومن غير ما أكدب عليكم، أنا ملكة السفرة المصرية.", "excited", "wearing a tiny crown, posing proudly"),
            _s("eggplant", "ملكة إيه يا ليمونة؟ ده إنتي كل ما حد يبص لك وشه بيتكرمش!", "sarcastic", "laughing and pointing"),
            _s("lemon", "عشان أنا قوية يا بتنجان، أنا اللي بفوق الفول والسلطة والطعمية.", "angry", "hands on hips, annoyed"),
            _s("eggplant", "وأنا بقى البابا غنوج والمسقعة والمخلل، يعني أنا الأكلة كلها يا هانم.", "happy", "twirling its mustache"),
            _s("lemon", "طب ما تيجي نتفق، أنا أتعصر على المسقعة بتاعتك وتبقى أحلى أكلة؟", "neutral", "thinking with a finger on her chin"),
            _s("eggplant", "اتفقنا! يا جماعة قولولنا، مين فينا أهم في المطبخ؟ أنا ولا الليمونة؟", "excited", "shouting happily at the camera"),
        ],
        "caption_ar": "مين ملك المطبخ بجد؟ 🍋🍆",
        "hashtags": ["خضار_بيتكلم", "كوميدي", "مطبخ_مصري", "fyp"],
    },
    {
        "title": "خناقة الخيارة والطماطم في السلطة",
        "format": "rivalry",
        "characters": [_c("cucumber"), _c("tomato")],
        "scenes": [
            _s("cucumber", "يا طماطم، اسمعي، في السلطة الخضرا أنا اللي بديها الانتعاش والقرمشة.", "neutral", "standing in a salad bowl, confident"),
            _s("tomato", "انتعاش إيه يا خيارة؟ الناس بتختار السلطة عشان لوني الأحمر الحلو.", "sarcastic", "showing off its shiny skin"),
            _s("cucumber", "لونك ده بيبوظ أول ما تتحطي في التلاجة يومين، وأنا لسه زي الفل.", "happy", "laughing and dancing"),
            _s("tomato", "طب ما الناس بتعمل مني عصير وصلصة وكاتشب، إنتي بيعملوا منك إيه؟", "angry", "frowning, leaves shaking"),
            _s("cucumber", "بيعملوا مني مخلل يا حبيبتي، وده أحلى حاجة على الفول الصبح!", "excited", "proudly holding a pickle jar"),
            _s("tomato", "خلاص يا خيارة، السلطة من غير الاتنين مش سلطة، تعالي نتصالح.", "happy", "smiling and hugging the cucumber"),
        ],
        "caption_ar": "السلطة من غير الاتنين مش سلطة 🥗",
        "hashtags": ["خضار_بيتكلم", "سلطة", "كوميدي", "fyp"],
    },
    {
        "title": "سر البصلة عشان متعيطش",
        "format": "advice",
        "characters": [_c("onion"), _c("potato")],
        "scenes": [
            _s("onion", "يا جماعة أنا البصلة، وعارفة إنكم كلكم بتعيطوا وإنتم بتقطعوني.", "sad", "looking guilty, holding a tissue"),
            _s("onion", "بس هقولكم سر صغير، حطوني في التلاجة ربع ساعة قبل ما تقطعوني.", "happy", "whispering a secret to the camera"),
            _s("potato", "وكمان استعملوا سكينة حامية، السكينة التلمة بتخلي البصلة تزعل وتعيطكم أكتر.", "neutral", "holding up a tiny knife like a teacher"),
            _s("onion", "بالظبط يا بطاطس، وبلاش تقطعوا الجذر من الأول، سيبوه للآخر.", "excited", "nodding happily"),
            _s("potato", "يعني من النهارده مفيش دموع في المطبخ، غير لما الأكل يبقى حلو قوي.", "happy", "laughing with joy"),
            _s("onion", "جربوا النصيحة دي وقولولنا في الكومنتات اشتغلت معاكم ولا لأ!", "excited", "waving at the camera"),
        ],
        "caption_ar": "مفيش دموع في المطبخ تاني 🧅 جربوها!",
        "hashtags": ["خضار_بيتكلم", "نصيحة", "مطبخ", "fyp"],
    },
]
