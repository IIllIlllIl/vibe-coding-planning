"""ACE review/curation transaction extracted from the formal playbook baseline.

Callbacks own artifact semantics and refinement. No evidence is committed until
the complete candidate has passed those callbacks.
"""
def run_ace_transaction(*, ledger, parent, records, reflect, validate_review,
                        curate, finalize, include_inactive=False):
    raw_reviews = list(reflect(records))
    reviews = [validate_review(raw, record) for record, raw in
               zip(records, raw_reviews, strict=True)]
    counted, pending = ledger.apply_reviews(parent, reviews)
    history = ledger.feedback_for(parent, reviews, records, include_inactive=include_inactive)
    delta = curate(counted, reviews, records, history)
    proposed = finalize(counted, delta, history)
    ledger.commit(counted, pending, feedback=history)
    return proposed
