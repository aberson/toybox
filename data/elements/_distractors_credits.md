# Distractor corpus — per-entry source attribution

`distractors.json` carries one entry per element of the form
`{ "element_id": "au-79", "fact_a_true": "...", "fact_b_false": "..." }`.
Each entry MUST have a matching row in the table below.

## Source tags

- **`operator`** — entry is human-authored or human-approved. Loader
  accepts unconditionally.
- **`llm`** — entry is machine-generated (Phase N Step N1.5 generator)
  and awaiting operator skim-review. Loader rejects these rows by
  default; set `TOYBOX_ALLOW_LLM_DISTRACTORS=1` to opt in (used while
  N1.5 has just run and N1 has not yet flipped tags).

## Format

Standard three-column markdown table:

```markdown
| element_id | source | reasoning |
|---|---|---|
| au-79 | operator | False fact "Gold floats in water" picked because Child B sees coins sink. |
```

Validation (run after every edit):

```
uv run python -m toybox.activities.distractor_corpus --validate
```

Successful output: `N entries, N credits rows, OK`.

## Entries

| element_id | source | reasoning |
|---|---|---|
| h-1 | llm | fact_b_false strategy: a, derived from he-2 fun_fact |
| he-2 | llm | fact_b_false strategy: a, derived from sr-38 fun_fact |
| li-3 | llm | fact_b_false strategy: a, derived from re-75 fun_fact |
| be-4 | llm | fact_b_false strategy: a, derived from cn-112 fun_fact |
| b-5 | llm | fact_b_false strategy: a, derived from ga-31 fun_fact |
| c-6 | llm | fact_b_false strategy: a, derived from er-68 fun_fact |
| n-7 | llm | fact_b_false strategy: a, derived from db-105 fun_fact |
| o-8 | llm | fact_b_false strategy: a, derived from cr-24 fun_fact |
| f-9 | llm | fact_b_false strategy: a, derived from pm-61 fun_fact |
| ne-10 | llm | fact_b_false strategy: a, derived from cf-98 fun_fact |
| na-11 | llm | fact_b_false strategy: a, derived from cl-17 fun_fact |
| mg-12 | llm | fact_b_false strategy: a, derived from xe-54 fun_fact |
| al-13 | llm | fact_b_false strategy: a, derived from pa-91 fun_fact |
| si-14 | llm | fact_b_false strategy: a, derived from ne-10 fun_fact |
| p-15 | llm | fact_b_false strategy: a, derived from ag-47 fun_fact |
| s-16 | llm | fact_b_false strategy: a, derived from po-84 fun_fact |
| cl-17 | llm | fact_b_false strategy: a, derived from li-3 fun_fact |
| ar-18 | llm | fact_b_false strategy: a, derived from zr-40 fun_fact |
| k-19 | llm | fact_b_false strategy: a, derived from ir-77 fun_fact |
| ca-20 | llm | fact_b_false strategy: a, derived from fl-114 fun_fact |
| sc-21 | llm | fact_b_false strategy: a, derived from as-33 fun_fact |
| ti-22 | llm | fact_b_false strategy: a, derived from yb-70 fun_fact |
| v-23 | llm | fact_b_false strategy: a, derived from bh-107 fun_fact |
| cr-24 | llm | fact_b_false strategy: a, derived from fe-26 fun_fact |
| mn-25 | llm | fact_b_false strategy: a, derived from eu-63 fun_fact |
| fe-26 | llm | fact_b_false strategy: a, derived from fm-100 fun_fact |
| co-27 | llm | fact_b_false strategy: a, derived from k-19 fun_fact |
| ni-28 | llm | fact_b_false strategy: a, derived from ba-56 fun_fact |
| cu-29 | llm | fact_b_false strategy: a, derived from np-93 fun_fact |
| zn-30 | llm | fact_b_false strategy: a, derived from mg-12 fun_fact |
| ga-31 | llm | fact_b_false strategy: a, derived from in-49 fun_fact |
| ge-32 | llm | fact_b_false strategy: a, derived from rn-86 fun_fact |
| as-33 | llm | fact_b_false strategy: a, derived from b-5 fun_fact |
| se-34 | llm | fact_b_false strategy: a, derived from mo-42 fun_fact |
| br-35 | llm | fact_b_false strategy: a, derived from au-79 fun_fact |
| kr-36 | llm | fact_b_false strategy: a, derived from lv-116 fun_fact |
| rb-37 | llm | fact_b_false strategy: a, derived from br-35 fun_fact |
| sr-38 | llm | fact_b_false strategy: a, derived from hf-72 fun_fact |
| y-39 | llm | fact_b_false strategy: a, derived from mt-109 fun_fact |
| zr-40 | llm | fact_b_false strategy: a, derived from ni-28 fun_fact |
| nb-41 | llm | fact_b_false strategy: a, derived from tb-65 fun_fact |
| mo-42 | llm | fact_b_false strategy: a, derived from no-102 fun_fact |
| tc-43 | llm | fact_b_false strategy: a, derived from sc-21 fun_fact |
| ru-44 | llm | fact_b_false strategy: a, derived from ce-58 fun_fact |
| rh-45 | llm | fact_b_false strategy: a, derived from am-95 fun_fact |
| pd-46 | llm | fact_b_false strategy: a, derived from si-14 fun_fact |
| ag-47 | llm | fact_b_false strategy: a, derived from sb-51 fun_fact |
| cd-48 | llm | fact_b_false strategy: a, derived from ra-88 fun_fact |
| in-49 | llm | fact_b_false strategy: a, derived from n-7 fun_fact |
| sn-50 | llm | fact_b_false strategy: a, derived from ru-44 fun_fact |
| sb-51 | llm | fact_b_false strategy: a, derived from tl-81 fun_fact |
| te-52 | llm | fact_b_false strategy: a, derived from og-118 fun_fact |
| i-53 | llm | fact_b_false strategy: a, derived from rb-37 fun_fact |
| xe-54 | llm | fact_b_false strategy: a, derived from w-74 fun_fact |
| cs-55 | llm | fact_b_false strategy: a, derived from rg-111 fun_fact |
| ba-56 | llm | fact_b_false strategy: a, derived from zn-30 fun_fact |
| la-57 | llm | fact_b_false strategy: a, derived from ho-67 fun_fact |
| ce-58 | llm | fact_b_false strategy: a, derived from rf-104 fun_fact |
| pr-59 | llm | fact_b_false strategy: a, derived from v-23 fun_fact |
| nd-60 | llm | fact_b_false strategy: a, derived from pm-61 fun_fact |
| pm-61 | llm | fact_b_false strategy: a, derived from bk-97 fun_fact |
| sm-62 | llm | fact_b_false strategy: a, derived from s-16 fun_fact |
| eu-63 | llm | fact_b_false strategy: a, derived from i-53 fun_fact |
| gd-64 | llm | fact_b_false strategy: a, derived from th-90 fun_fact |
| tb-65 | llm | fact_b_false strategy: a, derived from f-9 fun_fact |
| dy-66 | llm | fact_b_false strategy: a, derived from pd-46 fun_fact |
| ho-67 | llm | fact_b_false strategy: a, derived from bi-83 fun_fact |
| er-68 | llm | fact_b_false strategy: a, derived from he-2 fun_fact |
| tm-69 | llm | fact_b_false strategy: a, derived from y-39 fun_fact |
| yb-70 | llm | fact_b_false strategy: a, derived from os-76 fun_fact |
| lu-71 | llm | fact_b_false strategy: a, derived from nh-113 fun_fact |
| hf-72 | llm | fact_b_false strategy: a, derived from ge-32 fun_fact |
| ta-73 | llm | fact_b_false strategy: a, derived from tm-69 fun_fact |
| w-74 | llm | fact_b_false strategy: a, derived from sg-106 fun_fact |
| re-75 | llm | fact_b_false strategy: a, derived from mn-25 fun_fact |
| os-76 | llm | fact_b_false strategy: a, derived from sm-62 fun_fact |
| ir-77 | llm | fact_b_false strategy: a, derived from es-99 fun_fact |
| pt-78 | llm | fact_b_false strategy: a, derived from ar-18 fun_fact |
| au-79 | llm | fact_b_false strategy: a, derived from cs-55 fun_fact |
| hg-80 | llm | fact_b_false strategy: a, derived from u-92 fun_fact |
| tl-81 | llm | fact_b_false strategy: a, derived from na-11 fun_fact |
| pb-82 | llm | fact_b_false strategy: a, derived from cd-48 fun_fact |
| bi-83 | llm | fact_b_false strategy: a, derived from at-85 fun_fact |
| po-84 | llm | fact_b_false strategy: a, derived from be-4 fun_fact |
| at-85 | llm | fact_b_false strategy: a, derived from nb-41 fun_fact |
| rn-86 | llm | fact_b_false strategy: a, derived from pt-78 fun_fact |
| fr-87 | llm | fact_b_false strategy: a, derived from mc-115 fun_fact |
| ra-88 | llm | fact_b_false strategy: a, derived from se-34 fun_fact |
| ac-89 | llm | fact_b_false strategy: a, derived from lu-71 fun_fact |
| th-90 | llm | fact_b_false strategy: a, derived from hs-108 fun_fact |
| pa-91 | llm | fact_b_false strategy: a, derived from co-27 fun_fact |
| u-92 | llm | fact_b_false strategy: a, derived from gd-64 fun_fact |
| np-93 | llm | fact_b_false strategy: a, derived from md-101 fun_fact |
| pu-94 | llm | fact_b_false strategy: a, derived from ca-20 fun_fact |
| am-95 | llm | fact_b_false strategy: a, derived from la-57 fun_fact |
| cm-96 | llm | fact_b_false strategy: a, derived from pu-94 fun_fact |
| bk-97 | llm | fact_b_false strategy: a, derived from al-13 fun_fact |
| cf-98 | llm | fact_b_false strategy: a, derived from sn-50 fun_fact |
| es-99 | llm | fact_b_false strategy: a, derived from fr-87 fun_fact |
| fm-100 | llm | fact_b_false strategy: a, derived from c-6 fun_fact |
| md-101 | llm | fact_b_false strategy: a, derived from tc-43 fun_fact |
| no-102 | llm | fact_b_false strategy: a, derived from hg-80 fun_fact |
| lr-103 | llm | fact_b_false strategy: a, derived from ts-117 fun_fact |
| rf-104 | llm | fact_b_false strategy: a, derived from kr-36 fun_fact |
| db-105 | llm | fact_b_false strategy: a, derived from ta-73 fun_fact |
| sg-106 | llm | fact_b_false strategy: a, derived from ds-110 fun_fact |
| bh-107 | llm | fact_b_false strategy: a, derived from cu-29 fun_fact |
| hs-108 | llm | fact_b_false strategy: a, derived from dy-66 fun_fact |
| mt-109 | llm | fact_b_false strategy: a, derived from lr-103 fun_fact |
| ds-110 | llm | fact_b_false strategy: a, derived from ti-22 fun_fact |
| rg-111 | llm | fact_b_false strategy: a, derived from pr-59 fun_fact |
| cn-112 | llm | fact_b_false strategy: a, derived from cm-96 fun_fact |
| nh-113 | llm | fact_b_false strategy: a, derived from p-15 fun_fact |
| fl-114 | llm | fact_b_false strategy: a, derived from te-52 fun_fact |
| mc-115 | llm | fact_b_false strategy: a, derived from ac-89 fun_fact |
| lv-116 | llm | fact_b_false strategy: a, derived from o-8 fun_fact |
| ts-117 | llm | fact_b_false strategy: a, derived from rh-45 fun_fact |
| og-118 | llm | fact_b_false strategy: a, derived from pb-82 fun_fact |

## File history

- 2026-05-18 — Phase N Step N1-prep ships the empty scaffold. N1.5
  generator fills 118 rows tagged `llm`; N1 operator skim-review
  flips accepted rows to `operator` and edits/deletes rejects.
