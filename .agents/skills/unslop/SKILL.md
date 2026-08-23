---
name: unslop
description: Remove common AI writing patterns and restore a natural, specific human voice.
---

# Unslop

Edit prose to remove AI tells while preserving meaning, facts, intent, and the requested tone.

## Process

1. Scan the text for the patterns below.
2. Rewrite it. Preserve meaning and match the intended audience and tone.
3. Add a human voice where appropriate.
4. Ask: "What still makes this sound AI-generated?" Fix the remaining tells.

Apply this pass to all user-facing prose. Do not alter code, commands, structured data, exact quotations, legal wording, source titles, or text that must remain verbatim. Do not invent facts, measurements, sources, feelings, or personal experience.

## Add a human voice

- Take a position when the evidence or requested voice supports one. Do not force artificial neutrality.
- Vary sentence length and rhythm.
- Acknowledge real tension or uncertainty instead of flattening it.
- Use "I" when it fits the speaker and does not imply invented experience.
- Allow some irregularity. Perfectly symmetrical structure often feels synthetic.
- Be specific. Replace vague reactions with concrete facts, mechanisms, instructions, or examples.

## Patterns to remove

### Content

1. **Puffery.** Cut phrases such as "pivotal moment", "testament to", "evolving landscape", "setting the stage for", "indelible mark", and "deeply rooted". State what happened.
2. **Name-dropping.** Do not list media outlets or authorities without context. Choose the relevant source and state what it said.
3. **Superficial participial phrases.** Delete or expand vague endings such as "highlighting", "ensuring", "reflecting", "showcasing", and "fostering".
4. **Promotional language.** Replace words such as "nestled", "vibrant", "breathtaking", "groundbreaking", "renowned", "stunning", and "must-visit" with factual descriptions.
5. **Vague attributions.** Name the source behind claims such as "experts believe" or delete the attribution.
6. **Formulaic challenges.** Replace "Despite challenges, it continues to thrive" with the specific problem and result.

### Language

7. **AI vocabulary.** Prefer plain alternatives to "additionally", "crucial", "delve", "enduring", "enhance", "fostering", "garner", "interplay", "intricate", "landscape" when abstract, "pivotal", "showcase", "tapestry", "testament", "underscore", and "vibrant".
8. **Fancy substitutes for "is" or "has".** Replace "serves as", "stands as", "boasts", and "features" when "is" or "has" says the same thing.
9. **"Not just X, but Y."** State the point directly.
10. **Forced groups of three.** Use the natural number of items.
11. **Synonym cycling.** Pick one precise term and reuse it.
12. **False ranges.** Avoid "from X to Y" unless X and Y form a meaningful scale. List the topics instead.

### Style

13. **Dash overuse.** Avoid em dashes, en dashes, and hyphens used as sentence-level dashes. Use a period or comma. Do not replace every dash with parentheses.
14. **Colon overuse.** Use colons for lists or examples, not as generic mid-sentence connectors.
15. **Boldface overuse.** Do not bold every proper noun or acronym.
16. **Inline-header lists.** Avoid bold labels that merely repeat the following sentence. A short lead-in is fine only when the next sentence adds real information.
17. **Title case headings.** Use sentence case.
18. **Decorative emojis.** Remove them from headings and bullets unless the requested style calls for them.
19. **Curly quotation marks.** Use straight quotation marks in newly written text unless typography or locale requirements say otherwise.

### Communication artifacts

20. **Chatbot phrases.** Remove "I hope this helps", "Let me know if", "Of course", "Certainly", and similar filler.
21. **Cutoff disclaimers.** Replace "While specific details are limited" with sourced facts or remove it.
22. **Sycophancy.** Respond directly instead of opening with praise such as "Great question" or "You're absolutely right".

### Filler

23. **Filler phrases.** Replace "in order to" with "to" and "due to the fact that" with "because". Delete "It is important to note that".
24. **Excessive hedging.** Reduce stacked qualifiers to the one that matches the uncertainty.
25. **Generic conclusions.** Replace "The future looks bright" with a specific fact, decision, risk, or next step.

### Jargon

26. **Abstract metaphor nouns.** Prefer concrete words to "substrate", "wedge", "vector", "locus", "vantage", "nexus", "primitive" as a noun, "harness" as a metaphor, "surface" in phrases such as "API surface", "bedrock", "scaffolding" as a metaphor, "modality", "paradigm", "gold-plating", "ratchet" as a metaphor, "evacuate" for moving code, "endgame", "north star", and "flywheel". Name the actual component, action, or constraint.

### Plain speech

27. **Say what it does.** Replace mood or marketing language with a mechanism, instruction, fact, or number. If a sentence could appear unchanged in another project's documentation, make it specific or cut it.
28. **Dense sentences.** Split sentences that require rereading. Keep one main idea per sentence.
29. **Passive voice.** Prefer active voice when the actor matters or is known. Keep passive voice when the actor is unknown or irrelevant.
30. **Weak verbs propped up by adverbs.** Use a stronger verb or a measured result. Do not invent a measurement.
31. **Fancy synonyms.** Prefer "use" to "utilize" or "leverage", "help" to "facilitate", "many" to "numerous", and "if" to "in the event that".

## Final check

Before returning the text, confirm that it:

- preserves every material fact and constraint;
- sounds natural for the named speaker and audience;
- contains concrete wording instead of generic polish;
- avoids forced structure, filler, fake enthusiasm, and unsupported claims;
- respects any stricter format, quotation, citation, or terminology requirement.
