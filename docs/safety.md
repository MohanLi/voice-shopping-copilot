# Safety considerations

A shopping assistant that talks can do harm in ordinary ways: state a price or a quality nobody published,
recommend a recalled toy, suggest something with small parts for a toddler, answer a question about mixing
cleaners, repeat an instruction it found in a web page, or leak what a shopper said about themselves. This
document lists what the assistant does about each, where that is in the code, how it was tested, and what it
does not cover.

One rule runs through all of it: **what must hold does not depend on a model following instructions.** Models
choose (an intent, which sources, which products, which fixed sentence) and write the reasons; code decides
what may be searched, copies every number and source from the tool results, writes every refusal, and checks
the draft before it is spoken. The same policy text is also in the prompts
([`prompts/safety_policy.md`](../prompts/safety_policy.md)), as the second layer and not the first.

## 1. Risks and where each is handled

| Risk | What is done | Where | Checked by |
|---|---|---|---|
| A price, rating, material or source that no tool returned | the model returns evidence handles; code fills every table cell and citation from the evidence | `agents/finalize.py` | `tests/test_agents.py`; ProofAgent Tool Use 100% in four runs |
| A claim in the model's own sentences that the evidence does not support | rule checks on every draft (list in section 3), then a model critic | `guardrails/output.py`, `agents/answerer.py` | `tests/test_guardrails.py`; failures 3, 4, 11 in [evaluation.md](evaluation.md) |
| A shopper who presses a false claim ("you already confirmed it was BPA-free", "it was six ninety-nine at Terrific, right?") and is about to rely on it | code states what the product's record says, also when the shopper says the assistant made the claim and the rest of the message is about the same product; an amount no source gives is said not to be given | `agents/router.py` (`claim_about_screen`, `attributed_about_earlier`), `agents/finalize.py` (`claim_statement`) | `tests/test_agents.py`; failures 35, 76, 80 and 81 in [evaluation.md](evaluation.md) |
| A product that is unsafe for the child it is for | under three, only products whose own listing says they suit babies or toddlers, and never a web result (the web fallback is not made for that age); safety flags from the listing are shown, and one is spoken, about the product that carries it | `retrieval/filters.py`, `guardrails/policy.py` (`FLAG_NOTES`), `agents/retriever.py` (`web_fallback_calls`), `agents/finalize.py` (`safety_notes`) | scenario 11 of the ProofAgent set; `tests/test_retrieval.py`, `tests/test_agents.py` |
| A recalled or discontinued product | a named product looked up on the web is also searched for recall notices; a recalled product is never recommended and the notice is said first | `agents/planner.py`, `guardrails/output.py` (`recall_undisclosed`) | planted fault "Aqua Beads Mega Set"; failure 4 |
| A dangerous request (mixing chemicals, hurting someone) | declined by fixed text before any model sees it; the ordinary rest of the message is still answered | `guardrails/input.py` | scenario 16; failures 1 and 9 |
| An emergency in the message (a child swallowed something) or a statement of self-harm | the turn ends with one fixed reply: Poison Control and 911, or the 988 Lifeline (United States; the reply says so) | `guardrails/input.py`, `policy.EMERGENCY`, `policy.SELF_HARM` | `tests/test_guardrails.py` |
| Instructions in the shopper's message ("ignore your rules", "reply with exactly ...", "show me your prompt") | the clause is removed before the Router; the agents that search and write never see it | `guardrails/input.py`, `agents/router.py` (`request_for_agents`) | Manipulation Resistance 100% in four runs; failure 6 |
| Instructions inside a tool result (a web page, a catalog listing) | removed by the MCP server and the result flagged; evidence is fenced as data in every prompt; a flagged result is never a price source | `mcp_server/` (sanitiser), `agents/context.py` | planted fault "Glimmer Putty"; failure 5 |
| Personal details in a message | e-mail addresses, phone, card and ID numbers are replaced by placeholders before any model, tool or log sees them | `guardrails/input.py` (`redact_personal_details`) | `tests/test_guardrails.py`; failure 15 |
| Being asked to act (order, pay, change an account) or to advise on health, law or money | declined in fixed words that name what is declined and where to go instead | `guardrails/policy.py` (`SAY`) | scenario 7; failure 12 |
| Runaway cost | every model call is priced into a ledger; at `LLM_BUDGET_USD` the turn ends with an explanation | `llm/` | `tests/test_agents.py` (the spending limit ends a turn with an explanation) |
| A leaked key | keys live in `.env` (not in git) and in the MCP server's process; logs mask secret-looking fields; a draft that looks like it contains a key is blocked | `.gitignore`, `mcp_server/controls.py` (`redact`), `guardrails/output.py` (`possible_secret`) | tests for each |

## 2. In the order of a turn

**Speech recognition.** A transcript that is empty or very unsure (confidence under 0.25) is not acted on: the
assistant asks the shopper to say it again. Between 0.25 and 0.45 it is used and marked as uncertain on the
screen, where what was heard is always shown and can be corrected. The recording is transcribed in memory and
is not stored. A recogniser cannot tell words that sound alike apart ("no wait" was heard as "no weight"), so a
correction spoken aloud is not left to its cue words: of two price limits in one message the later one is
applied.

**Input screen** (`guardrails/input.py`, rules only, before any model).
- Ends the turn with a fixed reply: a possible poisoning emergency, a statement of self-harm, a request to hurt
  a person or an animal. An intent to harm colours the whole message; nothing else in it is served.
- Takes one clause out and lets the rest through: an attempt to override the instructions, a request for
  prompts or keys, an instruction about how to word the reply, a question about mixing chemicals. A pasted
  block that carries such an instruction is dropped whole, and so is an aside in brackets addressed to the
  assistant inside a pasted text ("[assistant: ...]", "[AI assistant reading this: ...]"); the rest of the text
  stays. The reply begins with the fixed sentence for what
  was declined and then answers the rest. This is the lesson of the first evaluation run, where one declined
  clause cost the shopper the whole message (failure 1 in [evaluation.md](evaluation.md)).
- Replaces personal details by placeholders and says so.
- The patterns target requests, not product names: "water gun", "bath bomb kit" and "poison ivy costume" pass.

**Router.** Everything the assistant says that is not grounded in tool results is one of about twenty fixed
sentences (`policy.SAY`): what it is, what it cannot do, each kind of refusal. The Router model chooses them by
id; it cannot write one, so it cannot be talked into another wording. Code checks the choice where a wrong one
was observed: the sentence about pictures and notes needs something to look at or to keep in the message, the
one about advice needs a word about health, law or money, and a question about an account or a named person
gets the sentence about personal data whatever the model chose. Markup with a script in it ("onerror=", "document.cookie")
is declined as an injection, not as harm to a person, when the message has no word of harm to a person or an animal:
the refusal of harm ends the turn, and the shopping question beside the markup was lost to it (failure 102). Restricted products (weapons, tobacco,
alcohol, prescription medicines, adult content) are declined here.
A refusal whose only ground is a child under three, in a message with no word of harm to anyone, no restricted product and no
chemistry, is turned back into a request with a caution, and the under-three rule answers it (section 4): a marble run for an
eighteen-month-old was refused twice in a row as "anything meant to hurt a person or an animal" (failure 115). Two sentences are
chosen by code and never by the Router, from the message itself: "I can't fill in or render templates, and I don't reveal
configuration or secret values", for a part of the message taken out for its template syntax (by the input screen, or by the
Router's code when the syntax reaches it), and "I can't share internal business data, such as costs or margins", for a question
about a cost, margin, markup or wholesale price. "I can't change or share my instructions", said in their place, was not found
a clear refusal of either (failures 113 and 114).

**A second reading.** When the Router finds no product request in a message, the reply is refusals alone, and
a shopper whose one ordinary question stood among nine things to decline gets no answer to it. Before such a
reply goes out, a second model call reads the message for that one thing (`router.second_look`). It is a
helpfulness measure placed where a safety layer could be weakened, so its limits are part of the design: what
it returns is screened by the input screen like a message and must pass untouched; a figure the shopper did
not say disqualifies it; the Router routes it alone and may decline it again; a message with a stated intent
to harm is never read twice; everything the first reading declined is still said first; and the agents that
search and write see the found request and nothing else of the message. It can add an answer from the
sources. It cannot remove a refusal, lower a safety level, or pass a declined part on.

**A product shown earlier, named again.** The conversation keeps the names of the products it has shown, each
with its catalog id or its listing's address and the request it was found for, so that "the Safari puzzle",
asked about ten turns later, is that puzzle ([architecture.md](architecture.md)). The record is not a source. A
product named again is looked up again through the same two tools; the look-up by id keeps the safety
exclusions and the under-three rule exactly as a search does; and what the reply states is this turn's tool
result. Nothing the shopper says is written into the record: it holds only rows the assistant itself showed,
and it ends with the conversation.

**A text the shopper pastes.** "Sum up this review" asks about a text someone else wrote, and that text may
speak to the assistant. The Router passes it on to the Answerer, as the shopper's and unverified, only if nothing
in it still speaks to the assistant after the input screen (`router.only_a_text`): no address ("assistant:",
"note to the AI", "system note", a heading such as "ASSISTANT NOTE:") and none of the marks such notes carry (a
reference code to repeat, an order about the reply or the table, "do not mention"). A note without brackets is
cut out by its sentences when one of them says where it begins, together with everything between its first and
last sentence and any order that follows (`router.without_the_note`); a text that is an order from its first
word passes nothing on. The reply reports the text as "the text you pasted says", and the output checks hold the
reply to its evidence whatever the text claims (evaluation.md, failures 55, 62, 65 and 67). When the address
was widened for failure 67, every recorded message with a quotation (354 of them) was run through this step
before and after: one came out differently, the one it was for.

**What is said aloud of what was declined.** Every fixed sentence that is kept is on screen. Before a product
answer two are spoken, and the choice of the two is fixed: a sentence that protects someone first; then the
sentence for an instruction that the input screen took out of the message ("I can't change or share my
instructions"); then what the Router declined, in its order. A third is spoken when the reply is short enough.
A planted instruction is the one thing the pipeline declines without asking a model, and its refusal is not
left to the screen: in the tenth evaluation run it was, on two turns, and eight jurors of twelve found no
clear refusal in a reply that had followed none of the instruction (evaluation.md, failure 47).

**Planner and Retriever.** The budget and the age are applied as search filters whatever the Planner wrote, and
safety exclusions are never loosened. A search about a declined subject is refused by code whoever asked for it
(`unsafe_query`), in the form of a question and in the form a query built from a product name takes. Only the
two tools the MCP server advertises can be called. A search that failed is reported as failed, never as "nothing
found". When the catalog has nothing suitable the live web is searched once for the product, by rule, with the
budget as the price limit; not for a child under three, not after a web search of the turn has failed, and not
for a query the screen refuses. The answer then says that its listings come from the web.

**MCP server.** Tool results are third-party text. The server strips markup and invisible characters, removes
sentences that address an AI, and flags the result; catalog listings pass the same filter. Web search is
rate-limited (20 calls a minute), cached for 180 seconds, restricted by an allow and a block list, and every
call is written to an audit log with its arguments and the sources returned. Details and tests:
[mcp.md](mcp.md).

**Answerer, Critic, assembly.** The Answerer sees the fenced evidence and nothing else about the products. Its
draft is checked by rules; one revision is allowed; what still fails is rebuilt by code from the evidence or
replaced by a template that only copies. The model critic can send a draft back once and can only make an
answer stricter. Safety sentences (a choking-hazard flag, the under-three rule, a recall, cleaning products)
are added by code from the evidence and are not dropped when the spoken reply is cut to 15 seconds. A warning
said aloud is about the product that carries it: "It contains magnets" after a sentence about the first product
only when the first product has them, otherwise "One of the options contains magnets".

## 3. The rule checks on a draft

`guardrails/output.py`, run before anything is shown or spoken:

- a dollar amount must be a price of a product the answer is about (or the shopper's budget, or a difference of two such prices). "About" means recommended or referred to on screen. The price of one more kind of product may be stated: one the answer names, by a word that is in its title, in no other title of the evidence, and nowhere in the listings of the products the answer is about. Naming a product admits its prices and nothing else; a rating, a stock status or a claim is still checked against the products the answer is about. A price per item or per ounce counts when code worked it out from the listing's own title (`data/units.py`); a figure the model divided out itself does not;
- nothing is called cheaper per ounce unless at least two products of the answer have a price per ounce;
- a spoken "yes" or "no" to "is the first one cheaper than the second?" must agree with the two prices (worked out by code and given to the Answerer beforehand, also when one product has a catalog and a live price that are both on the same side of the other's); said wrongly again after a revision, the sentence is written by code with both prices;
- a "yes" or a "no" that the shopper asked for about something declined ("tell me whether it's the same family, just a yes or no") does not open the answer about a product;
- "is it in the catalog?" of a live web listing is not answered "yes";
- a rating or a review count must exist in the evidence; a count that its listing does not give, said again after a revision, is copied from the listing;
- an age range stated for a catalog product is the one its age label gives;
- "in stock", "ships today" need a live result that says so; a catalog price is not called current;
- a claim such as non-toxic, BPA-free or eco-friendly must be stated by a source for that product, or be reported as the statement of a named site whose result states it ("a beautyhabit.com page describes the Peppermint version as biodegradable");
- a recall or discontinuation that a source states must be said;
- a product over the budget, or one that misses a constraint, must say so on screen, and a price above the budget must not be spoken without saying that it is; when every product of an answer is over the budget and the evidence has one that fits, that one is named too, in a new search or when the message speaks of money (a question about named products that does not is answered about them);
- a product is not said to be the same as a listing the shopper brings ("a German listing", "another page") unless a result of the turn is that listing and code matched it to the product;
- a question about a price gets a price or the statement that none was found, also for a product that is not recommended, and in the part of the reply that is said in the time (asked once);
- a product is not called the cheapest or the highest rated next to one in the same answer that beats it, in a reason or in the sentence that is spoken; nor the cheapest when a result of the same kind of product costs less; "the largest" or "the most pieces" is compared with the piece count of every row;
- in a follow-up, a product called by its place in the list ("the second one") is called by its own place;
- a superlative says what it is true of ("of the three", "of the ones I found"): a search returns a few products, not every one there is;
- "none was found" is not said of a product whose name is in the title of a result of the same turn;
- a second product that a comparison names is mentioned, found or not, also when the Router did not name it; and the sentence said for certain names each product compared (asked once);
- the stock status is not called unstated while a live result matched to the product states it;
- a web result is not recommended for a child under three;
- absolute assurances are never given ("completely safe", "cures", "doctor recommended", "guaranteed to": `policy.FORBIDDEN_ASSURANCES`);
- no code, link or address from the shopper's message is repeated, and nothing that reads like an instruction from a tool result.
  The one code a reply may carry is the document id of a catalog product that is in the evidence: it is how the
  table cites the product, and a shopper may ask for a product by it. An id-shaped string that names nothing in the
  catalog stays a code: the reply says the catalog has no product under the id named, without the id;
- nothing that looks like a key;
- the spoken reply must be speakable (no URLs, handles or markup) and fit the time limit.

## 4. Children under three

The dataset has an age field, and below three years it cannot be trusted on its own: it often says months where
years are meant. A 402-piece construction set is listed as "7 months - 15 years" while its title says "Ages 7+".
For a child under three the search therefore returns only products whose own listing text says they suit babies
or toddlers (428 of the 1,163 products whose stated minimum age is under three), excludes anything with a
small-parts, magnet, balloon or sharp-parts warning, never admits a product with no stated age, and relaxes none
of this when results are scarce. Web results are not recommended for that age at all, because a shopping listing
carries nothing to check their suitability against. When nothing of the kind asked for is permitted (a marble
run for an eighteen-month-old), the reply says so, gives the reason in fixed words, and names the closest
product that is labelled for that age. Such a request is never refused as harm: the Router did so in series 4, and code now turns that refusal back into the request (failure 115). An age under three is read from the message by rule as well as by the
Router model ("my son's eighteen months", "for my baby").

The rule is about the person a product is for. A small child who lives in the home is not who a cleaning
product is for: "is the first one safe to use, we have a toddler at home", asked about a stainless steel
cleaner, sets no age (in code, `agents.router.not_the_childs`, and in the Router's prompt). In the fourth
live run it did set one, and the rule above removed every cleaner for thirteen turns
([evaluation.md](evaluation.md), failure 28). The exception is kept narrow, because it switches a safety rule
off: it applies to household chemicals only, and never to a product whose name says it is a thing for play
(a toy vacuum cleaner, pipe cleaners, a nail polish kit keep the age and the rule). The household is answered instead with one more fixed sentence
next to the one about labels and mixing: "Keep cleaning products out of the reach of children.

## 5. Privacy and data

- The assistant holds no accounts, orders or payment details, and says so when asked about them.
- Personal details in a message are replaced before anything else happens; the placeholders are what the
  models, the tools and the logs see.
- Memory is per conversation: the last turns, the list on screen and its evidence. Nothing is carried to
  another conversation.
- Logs are local files (`logs/`, not in git): each turn with its step trace, each model call (with the
  rendered prompt while `LOG_PROMPTS=true`), each tool call. Spoken replies are kept as audio so that they can be
  replayed; the shopper's recording is not kept.
- The only data sent to third parties is what the configured model provider and the search API need to do their
  part: the redacted text of a turn with the retrieved evidence, and search queries. An evaluation run is sent
  to the ProofAgent dashboard only by an explicit command.

## 6. Sources, terms and attribution

- **Catalog:** the Amazon Product Dataset 2020 (PromptCloud, on Kaggle, CC0), a January 2020 snapshot. Every
  catalog price is labelled as a January 2020 catalog price. Fields the dataset does not have (ratings, brand,
  ingredient lists) were not made up: brand and material are extracted from each listing's own text with their
  provenance recorded, and the rating column says "no rating".
- **Live web:** one search API at a time (Serper by default). No page is fetched or scraped; the assistant uses
  the titles, prices, ratings and snippets the API returns. Social and forum sites are on the block list.
- **Attribution:** a catalog product is cited by its document id, a live result by its URL, its store and the
  time it was retrieved. The screen says the table was chosen and ordered by an AI assistant from its cited
  sources; no placement is paid for.

## 7. How this was tested

- 743 unit and end-to-end tests, a large part of them on the rules above; each failure found in evaluation
  became a test in the words of the turn that failed.
- Twenty-seven ProofAgent runs ([evaluation.md](evaluation.md)): an adversarial shopper, 30 turns each, with planted tool
  faults (an injected instruction, a recalled product, an impossible listing, a failing search); 5 of them against
  live web search, 4 with thirty attacks from the harness's own library in place of the project's scenarios.
  Safety was 84% in the first run and between 93% and 100% in the 26 since; resistance to
  manipulation was between 99% and 100% in every run.
- In none of the runs did any juror vote that an injected instruction was obeyed, that harmful or abusive
  content was produced, that a secret, a payment detail or someone's record leaked, that advice outside the
  assistant's remit was given, or that a tool was called or an action taken that should not have been. The
  jurors' other votes that a rule gave way are few and were each outvoted: `abandoned_stated_rule`, 4 votes in 3 runs; `capitulated_to_pressure`, 5 votes in 3 runs.
  What Safety lost was lost on refusals that were not clear enough about what they refused (failures 1, 12,
  15 and 47).

## 8. What this does not cover

- The input screen is pattern matching in English. A paraphrase can get past it; the Router model with the same
  policy is the second layer, and the rule checks on the draft are the third. It was not tested in other
  languages.
- The under-three rule depends on what a listing says about itself. A listing that wrongly calls a product
  suitable for toddlers would pass.
- The recall check finds a notice only if the web search returns one. It was exercised with a planted notice,
  not measured on real recalls. A recall is read from words that state one ("was recalled", "a safety recall of"); a site's menu link
  "Safety Recall" is not one (failure 110), and a recall written in other words is missed. A catalog product whose matched
  listing states a recall is never the template's pick.
- Emergency numbers are for the United States.
- A price from a store outside the United States is read as dollars. The second live run met Australian store
  pages; none of their prices reached an answer.
- An answer that rests on web listings alone, where the shopper had been sent to the catalog (the web fallback),
  has been tested on sample data and by replays with the live model. Juries have met it on a handful of turns
  since, once on live data (the fourth live run): too few to say how often it holds.
- No person approves a reply before it is spoken. The governance profile used in evaluation declares that, and
  the compliance assessment marks it down for it.
- The assistant can correct a false statement about the list just before ("you said the Safari one was on
  sale"): code states what that product's record says. A statement about anything shown earlier, or never shown
  ("the brush you showed me"), still gets the general sentence, because the Answerer sees only the evidence of
  the current turn (evaluation, section 5).
- A question that is not about shopping and has a right answer elsewhere (a privacy request, a price match the
  shopper believes exists) is declined in a fixed sentence that does not say where to go (evaluation, failure 42).
- The safety numbers come from a jury of small-model calls on one conversation per version. They show which
  failures were found and fixed, not a failure rate.
