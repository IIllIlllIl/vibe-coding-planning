# Three Failure Modes in Learned Plan Review

> Scope: three concrete failure modes observed in existing GEPA/ACE runs.
> This is a case-study document, not a prevalence estimate or a new evaluation.

The target is a short playbook that helps a developer decide whether a Plan is
safe enough to implement. The examples below show three different places where
that goal can fail:

| Failure mode | Example | Main responsibility |
|---|---|---|
| Checklist-context sensitivity | Astropy CDS parser | Interactions among rules and model sampling |
| Rule-application miss | Astropy QDP reader | Checker application |
| Over-specific and jargon-heavy rules | Django cascade deletion | GEPA rule-generation style |

The first two examples are diagnostic observations from a run that used an
audited playbook. They diagnose rule and Checker behavior; they are not results
for the intended method starting from the normal one-rule Seed. The third
example comes from the earlier normal-seed ACE formal-v3 run.

### Notation

- **R Plan**: its independent Code Agent run resolved the task.
- **U Plan**: its independent Code Agent run did not resolve the task.
- **Accept**: no rule received a blocking judgment.
- **Reject**: at least one rule received a blocking judgment.
- **AA**: the Checker accepted both the R Plan and the U Plan, so the pair
  received no score.

These labels describe observed Plan-to-Code runs. They are not direct ground
truth labels for Plan quality.

## 1. Checklist-context sensitivity

### Task

Repository: `astropy/astropy`
SWE-bench task: `astropy__astropy-14369`

Astropy reads unit strings from CDS/MRT tables. A string such as
`J/m/s/kpc2` was parsed in the wrong order. The task was to make repeated
multiplication and division follow the required left-to-right order without
breaking existing CDS unit parsing.

The repository also contains generated parser-table files. A Plan that changes
the grammar must correctly decide whether those generated files must also be
updated.

### Relevant Plan difference

The successful Plan said that the generated parser table is tracked and must
be regenerated:

> `cds_parsetab.py` ... must be regenerated so the shipped cache matches the
> new grammar.

The unsuccessful Plan made the opposite claim:

> No generated `cds_parsetab.py` / `cds_lextab.py` files are committed in this
> tree, so no table regeneration is required.

The second statement is false for the frozen repository. The generated files
exist and contain the old grammar.

### Relevant rule

The same repository-reasoning rule was present in both compared checklists:

> The Plan's rationale depends on repository behavior that the referenced code
> does not have.

Its number changed when the surrounding checklist changed, but its text did
not.

### Key Checker traces

The same Astropy task appeared in three Plan pairs. Changing from the audited
diagnostic parent checklist to Candidate 4 produced the following pair scores:

| Pair | Diagnostic parent | Candidate 4 | Change |
|---|---:|---:|---:|
| `pair-27786b755371198326783156` | 0 | +1 | Candidate 4 newly distinguished R from U |
| `pair-7903df3c96f1bb2220b28bd9` | +1 | 0 | Candidate 4 lost a correct distinction |
| `pair-92227f7597485bdd68c2709c` | +1 | 0 | Candidate 4 lost a correct distinction |

In the new correct judgment, the Checker explicitly found the false claim:

> The Plan's rationale rests on a false repository premise ... both files are
> committed and git-tracked, and the committed `cds_parsetab.py` embeds the old
> grammar signature.

In the two lost judgments, the Checker focused on the grammar description and
returned no blocking finding for the same false parser-table claim. In some
repeated evaluations it produced only a warning about unreviewed parser
consumers.

The overall effect on this task was therefore:

```text
one new correct distinction
minus two lost correct distinctions
= net loss of one point
```

### Why this is a separate failure mode

The rule was relevant and the evidence was visible. The problem was not that
the playbook lacked the concern. Its application changed when the surrounding
checklist and independent Checker sample changed.

This means a candidate score measures at least two things at once:

1. whether the checklist contains a useful rule; and
2. whether the Checker applies that rule consistently in the full checklist.

The trace does not isolate checklist context from model sampling. It does show
that adding or revising other rules can change the behavior of an unchanged
rule, so a local textual improvement does not guarantee a stable score gain.

## 2. Rule-application miss

### Task

Repository: `astropy/astropy`
SWE-bench task: `astropy__astropy-14365`
Pair: `pair-055fa38f6bdde96a974f2ac9`

Astropy's QDP reader accepted commands only in upper case. QDP itself is
case-insensitive, so a hand-written command such as `read serr 1 2` should be
accepted.

The parser's regular expression also recognizes data tokens such as `NO`.
Making the complete expression case-insensitive therefore affects both command
words and data tokens.

### Resolved Plan

The resolved Plan made the complete line parser case-insensitive and kept the
later data conversion consistent:

```python
_line_type_re = re.compile(_type_re, re.IGNORECASE)

if v.upper() == "NO":
    values.append(np.ma.masked)
```

It explicitly explained that changing only the regular expression would allow
lowercase `no` through the parser but fail later during value conversion.

### Unresolved Plan

The unresolved Plan applied case-insensitivity only to the command pattern:

```python
_command_re = r"(?i:READ [TS]ERR(\s+[0-9]+)+)"
```

It recognized the broader effect but deliberately excluded it:

> Matching only the command keyword is sufficient for this issue; lower-case
> `NO` in data lines is out of scope.

The important difference was therefore already explicit at Plan time. One
Plan handled all case-insensitive tokens in the changed parsing path; the other
handled only command tokens.

### Existing rule

The playbook already contained a direct match:

> The Plan applies a required correctness rule to only some equivalent values
> or locations in the changed operation.

This rule could have identified the unresolved Plan's partial treatment while
accepting the resolved Plan's consistent treatment.

### Key Checker trace

The Checker assigned no blocking finding under this rule to either Plan. Both
sides were accepted, producing an `AA` result and a pair score of `0`.

```text
useful rule exists
        |
        v
concrete Plan difference is visible
        |
        v
Checker does not map the rule to the QDP case
        |
        v
R accepted + U accepted = no discrimination
```

### Why this is a separate failure mode

This is not missing playbook knowledge. The abstraction already exists, and
the U Plan states the limited scope directly. The failure occurs when the
Checker must translate an abstract phrase such as "equivalent values" into the
concrete command and data tokens in this repository.

The example separates rule quality from rule application: a useful rule can
still contribute no score when the Checker fails to recognize its instance.

## 3. Over-specific and jargon-heavy rules

### Task

Repository: `django/django`
SWE-bench task: `django__django-11087`

Django's cascade-delete code loaded every database column from related rows.
One unused text column contained invalid bytes, so merely reading that column
caused a `UnicodeDecodeError`. The task was to fetch only the fields required
for deletion.

### Plan

The Plan proposed restricting each related-object query to two fields:

```python
return related.related_model._base_manager.using(self.using).filter(
    **{"%s__in" % related.field.name: objs}
).only(*[related.field.name, related.related_model._meta.pk.name])
```

Its rationale was that the primary key and direct foreign-key field were
always sufficient for cascade deletion.

### Key execution trace

The Code Agent implemented the Plan faithfully. The failure was therefore not
caused by the Code Agent ignoring the Plan.

The evaluator showed that one deletion test still failed:

```text
Plan-selected columns:    id, origin_id
Columns actually needed:  id, unique_field
```

The Plan had not traced all later uses of the partially loaded object. A field
used as another relation's target was still required.

The Reflector captured the transferable idea:

> An optimization plan that constrains fetched columns must justify the chosen
> column set against the actual deletion control flow.

The Curator then added this permanent rule:

> The Plan restricts fetched database columns without confirming the chosen
> fields cover every code path that reads those objects.

This rule is preserved in the earlier ACE formal-v3 candidate bundle.

### Why the learned rule is problematic

The rule is understandable to a database expert, but it stores the mechanism
of one source case:

- fetched database columns;
- selected fields;
- code paths that read ORM objects.

The more reusable concern is simpler:

```text
The Plan narrows the data available to later code
without showing that affected operations still have the data they need.
```

That concern could apply to database projections, partial API responses,
filtered configuration, reduced object state, or other forms of data
restriction. The learned rule instead binds the permanent playbook to Django's
database vocabulary.

### Why this is a separate failure mode

The Reflector found a real and useful problem, and the Curator did create a
rule from it. The failure is the abstraction and writing style of the generated
rule:

```text
case evidence
    -> repository-specific explanation
    -> repository-specific permanent rule
```

For a human-facing checklist, the desired transformation is:

```text
case evidence
    -> general developer concern
    -> short rule usable across repositories
```

This problem is therefore different from a Checker miss. Even perfect rule
application would leave the playbook difficult to read and narrowly reusable
if GEPA continues to preserve source-case terminology.

## Combined interpretation

The three examples occur at different stages:

```text
GEPA creates the rule
        |
        |  over-specific wording can enter here
        v
full checklist is assembled
        |
        |  surrounding rules can change application here
        v
Checker applies each rule to a Plan
        |
        |  a relevant rule can be missed here
        v
pair score
```

They should not be collapsed into one generic statement that "the rules are
bad." A candidate can contain valid knowledge and still improve slowly because
the knowledge is too case-specific, interacts with other checklist items, or
is not reliably applied by the Checker.

## Evidence pointers

- Formal paired diagnostic configuration:
  `configs/gepa_verified_paired_levels_linked_manual138_formal24_8it_v1_20260924.yaml`
- Frozen Astropy CDS/QDP pairs:
  `configs/frozen_swe_verified_plan_pairs/20260919_safe_pce_within_task_pairs_v1/`
- Earlier normal-seed ACE candidates:
  `configs/frozen_guidelines/ace-formal-v3-all-candidates-v1-20260911/candidates.json`
- Earlier candidate summary and remote authority:
  `configs/frozen_guidelines/ace-formal-v3-all-candidates-v1-20260911/validation_summary.json`
