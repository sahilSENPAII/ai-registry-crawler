# Demo script

A walkthrough of about 3 to 4 minutes for a screen recording. Start with the stack running
(`docker compose up --build`) and the dashboard open at http://localhost:3000.

## 1. Overview (30 s)

**Show:** the stat tiles, then the "Risk across the AI estate" bar.

**Say:** "This is every AI system in the AWS account: 11 out of 15 workloads, 4 of them agents.
Four are high risk, there are three critical or high issues, and one agent is running that
nobody registered. Switching models as recommended would save about $188 a month."

## 2. High-risk systems (20 s)

**Show:** the "High-risk systems" list under the risk bar. Point at the owners and the scores.

**Say:** "Every high-risk system is an agent, and each has a named owner from its tags. Let's
open the refund agent."

**Click:** refund-agent.

## 3. The refund agent's record (45 s)

**Show:** "Why we classified it this way", then "Risk", then "ISO/IEC 42001 controls".

**Say:** "It is an agent because its code declares tools, loops on the model's tool requests,
keeps session memory and has a step limit. That adds up to 0.9 against a threshold of 0.6, and
each line is the evidence. It scores 8 risk points: an agent that issues refunds and sends
email, on pii, customer-facing, in prod. That brings in the impact assessment controls A.5.2
and A.5.4, plus monitoring and event logs for agents, A.6.2.6 and A.6.2.8."

**Click:** "View run" under "Took an action without approval" in the Issues card.

## 4. The $1,200 run (40 s)

**Show:** the run waterfall. Point at the `issue_refund` step with $1,200.00 and "Changes data".

**Say:** "This is one run, step by step: model calls in blue, tools in grey. The agent looked up
the order and refunded $1,200. Its approval threshold is $500, and there is no approval step
before the refund, so this is the critical issue."

**Click:** the `issue_refund` row to show its recorded attributes, including the amount.

## 5. Issues (35 s)

**Click:** Issues in the sidebar.

**Show:** the critical issue, then the two high ones.

**Say:** "Issues are grouped by severity. Here is a shadow agent: marketing-copy-agent wrote 52
spans of activity but is in no inventory. And the sales research agent called
`export_contacts_csv`, a tool it never declared. Each issue links to the agent and the exact run.
The approved $25,000 purchase order in the logs is deliberately not on this list."

## 6. Model fit (35 s)

**Click:** Model fit in the sidebar.

**Show:** the total savings, then hr-policy-assistant, then sales-research-agent.

**Say:** "For each system, the model that fits its task. The HR assistant gets an upgrade: Claude
3 Sonnet is below the capability bar for agent work. The sales agent sends confidential data to
an external API, so it is moved to a Bedrock model even though that costs more. Data residency
is a hard rule, not a trade-off. Prices are illustrative."

## 7. Run discovery (20 s)

**Click:** "Run discovery" at the bottom of the sidebar.

**Show:** the button changes to "Running discovery", then the line under it reads "Done: 11 AI
systems, 7 issues" and "Last discovery run" updates.

**Say:** "Discovery crawls the account, reads the agent logs and refreshes the recommendations
in one run. Reruns update the same records, and anything that disappears is marked retired."
