<!-- SPDX-FileCopyrightText: 2026 academic-pdf-en-zh-reader contributors -->
<!-- SPDX-License-Identifier: Apache-2.0 -->

# Windows handle-count regression in v0.2.1

The v0.2.1 full Windows suite reported one extra parent-process handle during
repeated output-limit failures. The same case passed in isolation. Running the
whole worker test module locally reproduced the increase during the CPU-limit
case instead.

A process-handle snapshot identified a new native thread whose start address
belongs to `ntdll.dll`, a new event, and a closed ALPC port. No Python pipe-reader
thread remained. A separate process that called `LookupPrivilegeValueW` once,
without starting any worker, reproduced exactly that change after about 30
seconds. A token-creation-only control did not reproduce it on the same host.
The host LUID verification test had started this delayed Windows RPC activity
in the shared pytest process before the strict worker handle-count tests.

The host LUID test now performs the same real API query in a bounded subprocess,
checks its successful exit and exact returned LUID, and waits for it to finish.
Its RPC state cannot outlive that subprocess in the pytest process. The worker
handle tests retain their original zero-growth assertions, including rejection
of a single transient increase and rejection of growth after an earlier drop.
No additional handle allowance, retries, or sleeps were added to those tests.

The token creation path also no longer resolves unused privilege names for an
unrestricted source token. It still applies `DISABLE_MAX_PRIVILEGE`, validates
the resulting restricted token, and checks the unchanged enabled-privilege
allowlist. An already restricted source is checked before duplication, and its
duplicate is checked again before it is returned.

A deterministic regression additionally exposed an actual ownership gap: if
querying the newly created token's privileges raised, the token handle was not
closed. All failures during post-creation token validation now close that owned
handle before propagating the error. Tests cover query errors, unexpected
privileges, a non-restricted result, and both inherited-token privilege checks.

These changes are released as v0.2.2; v0.2.1 tags and asset bytes are preserved.
The continuous layout and typography policies introduced in v0.2.1 are unchanged.

The next full CI run passed the original handle-count assertions but exposed a
separate ambiguity in the inheritance probe: the child's handle number could
refer to its own unrelated event. Resetting and setting that event inside the
child was insufficient to establish whether the parent's object was inherited.
The probe now creates an initially unsignaled manual-reset event, asks the child
to signal its candidate handle, and checks the still-open event in the parent.
An unrelated child event leaves the parent's event unsignaled; an actually
inherited event signals the parent and fails the allowlist gate. Missing,
unexpected, or contradictory evidence still fails closed. Tests include a real
process launched with the test event deliberately added to the inheritance list,
as well as the normal three-handle list and deterministic unrelated-event cases.
