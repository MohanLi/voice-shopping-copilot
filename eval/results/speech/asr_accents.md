Speech recognition on synthetic accents: 10 shopping requests read by 17 voices (295 clips), recognised by Whisper small.en (real-time factor 0.417). The voices were trained on speakers of other languages and read English here; they are a stand-in for accents, not a sample of people. "Kept" means the value extracted from the transcript equals the value extracted from the text that was read.

| Voice group | clips | WER % | budget kept % | age kept % | brand kept % | usable % | confidence |
|---|---:|---:|---:|---:|---:|---:|---:|
| American English | 20 | 0.0 | 100.0 | 100.0 | 100.0 | 100.0 | 0.817 |
| British English | 20 | 0.3 | 100.0 | 100.0 | 100.0 | 100.0 | 0.832 |
| Spanish-accented | 20 | 0.0 | 100.0 | 100.0 | 100.0 | 100.0 | 0.834 |
| French-accented | 10 | 3.9 | 100.0 | 100.0 | 100.0 | 100.0 | 0.818 |
| Hindi-accented | 20 | 0.7 | 100.0 | 100.0 | 100.0 | 100.0 | 0.822 |
| Italian-accented | 20 | 0.3 | 100.0 | 100.0 | 100.0 | 100.0 | 0.833 |
| Japanese-accented | 20 | 0.8 | 100.0 | 100.0 | 100.0 | 100.0 | 0.821 |
| Portuguese-accented | 20 | 0.0 | 100.0 | 100.0 | 100.0 | 100.0 | 0.832 |
| Mandarin-accented | 20 | 1.9 | 100.0 | 100.0 | 100.0 | 100.0 | 0.821 |
| All groups | 170 | 0.7 | 100.0 | 100.0 | 100.0 | 100.0 | 0.826 |

Speech variations (American voices):

| Condition | clips | WER % | budget kept % | age kept % | brand kept % | usable % | confidence |
|---|---:|---:|---:|---:|---:|---:|---:|
| clean | 20 | 0.0 | 100.0 | 100.0 | 100.0 | 100.0 | 0.817 |
| fast (1.25x) | 20 | 0.4 | 100.0 | 100.0 | 100.0 | 100.0 | 0.813 |
| noisy (10 dB SNR) | 20 | 0.6 | 100.0 | 100.0 | 100.0 | 100.0 | 0.786 |

Requests that name a brand, with and without the catalog's brand names given to the recogniser:

| Recogniser | clips | WER % | budget kept % | age kept % | brand kept % | usable % | confidence |
|---|---:|---:|---:|---:|---:|---:|---:|
| with the brand hint | 85 | 0.7 | 100.0 | 100.0 | 100.0 | 100.0 | 0.833 |
| without it | 85 | 2.3 | 100.0 | 100.0 | 94.1 | 100.0 | 0.783 |
