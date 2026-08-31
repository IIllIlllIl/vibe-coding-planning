1. The plan identifies specific plausible root-cause hypotheses grounded in the reported behavior and existing codebase.

2. The plan names relevant files, modules, functions, interfaces, configuration, or data paths that can be inspected at the base commit.

3. The plan explains how the issue's symptoms could arise from existing control flow, data flow, state handling, dependency behavior, or configuration.

4. The plan addresses all major behaviors, requirements, error modes, and constraints stated in the issue description.

5. The plan distinguishes confirmed facts from assumptions and proposes ways to validate important assumptions before major changes.

6. The plan includes investigation steps that could falsify competing hypotheses using only the issue description and repository state.

7. The plan considers relevant edge cases, boundary conditions, input variants, environment constraints, or compatibility concerns implied by the issue.

8. The plan's proposed change scope is proportional to the evidence and does not introduce broad rewrites without repository-grounded justification.

9. The plan is specific enough that it would not apply equally well to many unrelated issues.

10. The plan is internally consistent and does not contradict the issue description, repository structure, existing interfaces, or documented behavior.

11. The plan accounts for interactions between affected components rather than treating the issue as isolated when the repository indicates dependencies.

12. The plan identifies validation approaches tied to the reported behavior, without relying on future execution results or known outcomes.

13. The plan avoids unsupported claims about the cause, fix, or correctness when the repository evidence has not yet been inspected.

14. The plan prioritizes understanding the existing implementation before proposing invasive modifications.

15. The plan avoids changes unrelated to the reported issue or speculative improvements not needed to resolve the described behavior.

16. The plan recognizes relevant tests, examples, fixtures, documentation, or existing usage patterns in the repository that can inform investigation.

17. The plan preserves existing public interfaces, documented behavior, and compatibility unless the issue explicitly requires changing them.

18. The plan provides a coherent sequence from investigation to hypothesis confirmation to narrowly targeted modification and validation.
