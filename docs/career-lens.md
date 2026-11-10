# My Career, qualification, Pipeline and Today

**Proposed product design.** When the owner opens the job-search workspace, they should be able to see what the agent understands about their career, how that understanding affected a decision, and where their time would help most today. The owner can correct the agent in ordinary language. Routine research and qualification happen without a review queue.

## How the brain and CRM work together

| Question | Authority | How the app answers |
| --- | --- | --- |
| What work does the owner want? What have they done? | Private brain, with dated source references | Retrieve relevant passages and current beliefs, including which statements came from the owner and which are inferred. |
| Who knows whom? | Identified people and documented relationships | Show the path and supporting evidence. Similar experience can suggest someone to research. A warm path requires evidence of a relationship. |
| What happened in this search? | Private CRM | Use canonical company, person, role and activity records, including application stages, timestamps and revisions. |
| What fits, what changed, and what should happen next? | Derived assessments tied to brain and CRM revisions | Explain the decision, show its inputs, and refresh it when a relevant input changes. |

A **lens** is a private view that selects relevant memories, connects them to records, and answers a defined set of questions. The reusable part is the ability to follow a conclusion back to a source, correct it, and find the decisions that used it. Each lens defines its own questions and counting rules. Editing an application stage updates its CRM record. Correcting a preference revises the brain and can trigger new assessments.

The current [workspace](job-search-workspace.md) already has dossiers, evidence and revision checks. An application record can represent a role under consideration before submission. Use that identity across discovery and application so a job keeps its evidence when its stage changes. The current gbrain adapter retrieves passages by keyword. Proposed structured reads include current claims about a subject, their sources, and documented links between identified entities. Validate those reads against the installed gbrain version before choosing native graph queries or a rebuildable private index. Exact dates, distinct-company counts and workflow stages come from CRM queries. Semantic search is an optional way to discover relevant evidence.

## My Career

The first lens answers three questions on one page:

1. **What am I looking for?** Show a freeform search brief. Preserve the difference between requirements, targets, acceptable alternatives and open questions. The owner can express a tradeoff in a sentence.
2. **What have I done?** Show dated roles, projects, contributions, outcomes and relationships with links to the evidence. Distinguish personal contributions from team results and past work from future interests.
3. **How has this affected my search?** From a belief, open the companies, contacts and jobs whose assessments relied on it. From a decision, open the relevant passage or correction.

Use available private evidence, including authorized Grokbot and Muse imports, before asking the owner. Record authorship, source, date, and whether a claim is explicit or inferred. A provider's suggestion is evidence of what it recommended. The owner's own words establish an expressed preference. Give a current explicit correction precedence over an older inference, retain the earlier version, and show which belief is current. Ask a focused question when resolving an ambiguity would change a real decision and existing evidence cannot resolve it. A silent skip supplies little evidence about a general preference.

## Qualification

On intake, resolve identity, look up previous decisions, and assess each record against the current brief. Score companies for pursuit, jobs for fit and contacts for relevance to a particular pursuit. Report a disposition (pursue, research, or disqualified), relative priority, reason, supporting evidence and material unknowns. Confidence in the evidence and attractiveness of the opportunity answer different questions. Ranking should make room for a promising job with incomplete information.

For compensation, compare a known base range with the owner's private base floor and target. A maximum below the floor can disqualify the job. A range spanning the floor calls for assessing likely placement. An undisclosed range or a total-compensation figure alone leaves base unknown. If the work otherwise fits, the agent should investigate and record what it finds. Apply the same distinction between missing evidence and a known mismatch to location and role scope.

Retain disqualified records, aliases, scope of the decision and the reason. A duplicate import reuses the existing decision. A rejected job can belong to a promising company, and a contact who is irrelevant to one role may help with another. When evidence or a preference changes, revisit affected records, including disqualifications. Preserve explicit owner decisions and surface new evidence that warrants reconsideration. Record which revisions an assessment used so an older result cannot replace a correction.

## Pipeline

Pipeline answers where the search has enough options, where it is progressing, and where it needs work. Show the underlying records behind each number. The proposed starting targets are adjustable:

| Measure | Starting target | Counting rule |
| --- | --- | --- |
| Independent options | 3 companies | Count each active company once when a role is under discussion or there is a concrete agreed next step. A qualified listing alone is a sourcing opportunity. |
| Recent progress | 2 companies in 14 days | Count a completed warm introduction, role discussion, scheduled interview or comparable advance, using the event date. An introduction can count here before the company qualifies as an independent option. |
| Replenishment | 2 promising jobs or companies reviewed and 2 warm outreach attempts per week | Count distinct reviewed opportunities or targets and actual outgoing outreach. Drafts are pending work. Show replies separately. |

Keep sourcing and warm outreach going during interviews. Increase the work that addresses the actual shortage. Few credible options call for company research or job discovery. Several attractive roles with no known path call for contact sourcing. A known connection makes warm outreach actionable. Qualified openings with a closing date may warrant applying directly. Report source coverage and last successful check so an empty feed distinguishes a checked source with no promising matches from a source that has stopped supplying listings.

## Today

Generate a short list from current commitments and gaps across the pipeline. Rank dated commitments first, then expiring opportunities, then actions addressing the largest gap. Each item names an action, a person or role where relevant, why it matters now, and the evidence behind it. Let the owner open the record, correct the suggestion, or defer it to a date.

Reconcile related activities before recommending an action. An availability request, the owner's reply and an interview confirmation can come from different people. The application should now suggest preparing for the interview. A separate promise to send a work sample remains open. Show the same commitment once in Today even when it appears on the person, company and application pages. When someone else owns the next move, show the expected date and surface follow-up when due. Qualification or a recommendation by itself does not send a message or submit an application.

## Review scenarios and build decision

- An imported conversation supports a narrow role preference. The owner later broadens it in My Career. Jobs set aside for that reason get reassessed, the owner can inspect why their ranking changed, and the company count changes only with qualifying activity.
- A duplicate listing with the same known mismatch stays disqualified. A revised posting that removes the mismatch reopens assessment on the same record. Other roles at the company retain their own assessments.
- An introduction advances the momentum measure. A subsequently agreed hiring conversation adds the company to independent options. A second role at the company does not increment that count.
- During an interview week, Today shows a dated preparation task and a concrete outreach action if weekly replenishment is behind. A completed reply removes the corresponding task across views.

The first implementation decision is which uncertainty to test. A read-only My Career view tests whether existing evidence produces an accurate brief. One preference correction flowing through qualification tests whether decisions can be explained and revised. A Pipeline and Today view over existing CRM evidence tests whether the counts and recommendations change how the owner spends time. Each slice can use synthetic fixtures for verification. The choice of first slice, storage for derived claims, and any dedicated classifier or embedding model remain open.
