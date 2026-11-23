You are storyboarding Part {{part}} ("{{part_title}}") of the video. The narration is already split into numbered beats. Turn the beats into scenes.

Rules
- Cover every beat of this part exactly once, in order. A scene takes 1 beat, or 2 consecutive short beats, so each scene lasts about 4-6 seconds (10-16 spoken words). Never more than 2 beats.
- Scene types:
  - "still": one Midjourney image; the editor adds a slow camera move. Most scenes.
  - "motion": one Midjourney image that is animated in Kling (image to video). Use for the strongest moments only, at most {{max_motion}} in this part.
  - "card": a graphic card rendered automatically, for hard facts: an exact amount, a date, a verbatim quote, a line from a document, a short list, or a comparison. About one scene in five, never two cards in a row.
  - "reuse": show the image of an earlier scene of this part again with a different camera move, when the narration stays on the same subject. Set reuse_of to that scene's number within this part (1 = first scene of this part).
- Budget: at most {{max_images}} scenes of type still or motion in this part (each needs a new image).
- mj_prompt (still and motion only): 20-45 words describing subject, setting, composition, lighting and mood. Use the visual bible's reference names and looks for people and places, never real names, logos, brand names or readable text. Stylized, cinematic, not photo-real. No style words or parameters at the end; they are added automatically.
- kling_prompt (motion only): 8-25 words on camera movement and subtle motion in the image (slow dolly in, rain on glass, papers drifting, screens flickering). Slow and physically plausible.
- camera (still and reuse): push_in, pull_out, pan_left, pan_right, pan_up, pan_down or static. Vary it from scene to scene.
- Cards: card_kind is one of stat, quote, date, title, text, document, list, compare.
  - stat: card_text is the number as it should appear ("$8,000,000,000", "1.2M", "97%"), card_sub says what it is (under 60 characters).
  - quote: card_text is the verbatim quote (under 160 characters), card_sub is "Name, setting, year".
  - date: card_text like "NOVEMBER 8, 2022", card_sub is what happened (under 60 characters).
  - document: card_sub is the document name ("SEC complaint, Dec 2022"), card_items are 2-5 short lines from it, card_text is the line to highlight.
  - list: card_text is a short heading, card_items are 2-4 items under 40 characters each.
  - compare: card_items are exactly 2 items in the form "Label: value", card_text is the heading.
  - title or text: card_text under 40 characters, card_sub optional.
  - card_tone: "loss" for money lost or crimes, "gain" for money raised or growth, "alert" for warnings, otherwise "neutral".
  Card text must be facts stated in the narration of that scene.
- onscreen: optional lower-third for a place or date ("Nassau, Bahamas - 2021"), under 40 characters. Use sparingly; empty otherwise.
- visual: one plain line describing the shot for the editor.
- Fields that do not apply to the scene type are empty strings, empty lists or 0.

Visual bible:
{{bible}}

Beats of Part {{part}}:
{{beats}}
