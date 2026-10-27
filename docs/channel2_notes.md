# Channel 2 idea: notes from @eslamelk7lawy1 (7kawy AI)

Studied 2026-10-27, on John's request. For a possible **second channel** once the first one works.
The first channel stays as it is (the fridge gang, Egyptian comedy).

## What the account is
- Instagram `eslamelk7lawy1`: **1.2M followers, 694 posts**, marked "AI-generated profile".
- Bio: "Founder: 7kawy AI | SMSM AI | El ganyas | Elsmsm | super nova | tafra", 1M+ on YouTube.
  So it's a network of several channels in the same style.
- Watermark on the videos: "supernova".

## The formula (seen in every post)
- **Always a 2-part series.** The caption is "<emotional title>😱💔 الجزء الاول" and then "الجزء الثاني".
  Recent titles:
  - عامل النظافة كسب بطل العالم تحت الماء
  - استهانوا بيه في المدرسة عشان يتيم
  - سرق مجهود صاحبه في الامتحان
  - اشترى روبوت خردة وكسب بيه البطولة
  - كانوا بيستغلوها وياخدوا خيرها
  - المقص السحري والشعر المعجزة
- **Story type: the underdog.** A poor or bullied hero (a cleaner, an orphan, a student) against a rich,
  arrogant villain. The hero ends up winning a competition or championship, and justice comes at the end.
  The audience gets emotional (💔) and feels it's fair.
- **Length: ~99 seconds.** That's longer than our 30–60.
- **Hashtags:** #فواكه #aiphoto #قطط #catolicos (they mix cats in too).
- **Comment seen:** "نفس القصه تاني؟". The audience notices the formula repeating, so variety still matters.

## The look
- Fruits with **full human bodies**: clothes, uniforms, muscles. A rambutan as a cleaner in a diving
  suit, a plum in a gold robe, an orange in a white suit, a strawberry as a TV presenter in sequins, and
  a muscular durian as the champion.
- **The same character keeps exactly the same look in every scene**: the hero's hair and the badge with
  his number stay identical.
- **Every character wears a sign of their "value"**: the hero "1", the villain "100,000", the champion
  "1,000,000". That visual gimmick makes status obvious at a glance.
- Cinematic, luxurious places: a hotel pool, a palace-like office, a stadium with an audience.
- **Arabic text inside the pictures** (for example "بطولة حبس النفس تحت الماء", "النادي الفلكي للسياحة").
  That means they use an image model that writes Arabic.
- It's a **still-picture slideshow with a voice-over and music**. It isn't real video either.
  So our CPU approach (picture per scene + camera motion) is close to what they do.

## What a second channel would need from us
1. **Story prompt:** an underdog 2-part arc, a villain with a "value" badge, a competition, a twist,
   and justice at the end.
2. **Look:** fruits with human bodies and outfits (a change to SCENE_STYLE), fixed character design
   sheets, and the value badge.
3. **Arabic text inside the pictures:** FLUX schnell / Playground can't write Arabic. Options:
   - Write it ourselves as an overlay (libass), which also lets us control the spelling.
   - Use an image model that writes Arabic (not free on our Gemini key).
4. **Video length:** 80–100 s, which means longer scripts and more scenes.
5. **Narration:** they seem to use one narrator (I couldn't check the sound because the video was
   muted). We have per-character voices, and a narrator voice would be easy to add.
