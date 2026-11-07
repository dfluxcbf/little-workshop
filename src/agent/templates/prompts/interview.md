This task is the project's start: before any code, interview the owner about what the project is for and write it down.

1. Read what the scaffold left (`README.md`, `docs/design.md` if there is one) so you do not ask what is already written.
2. Interview the owner. This session runs under Remote Control, so they answer from the Claude app or their phone: ask through your user-messaging tool. {interview_method} Ask one question at a time and wait for its answer before the next: for these questions ground rule 7's "never block" does not apply. Cover the purpose, who uses it and how, the core concepts, what is in and out of scope, the constraints (platform, stack, data, hosting) and the first milestone. When an answer is vague, ask a follow-up rather than guessing. If no answer has arrived for a long while, write down what you have, list the rest as open questions and finish with `partial`.
3. Write `docs/design.md` from the answers: purpose, users, core concepts, scope and non-goals, key decisions with their reasons, the first milestone, and open questions. Use the owner's words where they are precise; never invent a decision they did not make.
4. {requirements_step}
5. No code. Commit the documents on your branch.
