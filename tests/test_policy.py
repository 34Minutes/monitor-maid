from monitor_maid.models import Instruction, SafetyAssessment
from monitor_maid.policy import (
    apply_review_to_plan,
    assess_instruction,
    catastrophic_reasons,
    compile_denylist,
    parse_verdict,
    plan_approval,
    plan_relay,
    DEFAULT_DENYLIST,
)


def _shell(command: str, **kwargs) -> Instruction:
    return Instruction(op="shell", raw=command, command=command, **kwargs)


def test_catastrophic_rm_variants():
    assert catastrophic_reasons("rm -rf /")
    assert catastrophic_reasons("rm -fr /")
    assert catastrophic_reasons("rm -rf ~")
    assert catastrophic_reasons("rm -rf $HOME")
    assert catastrophic_reasons("rm -rf /usr")
    assert not catastrophic_reasons("rm -rf ./build")
    assert not catastrophic_reasons("rm -rf /tmp/job")
    assert not catastrophic_reasons("rm file.txt")
    assert not catastrophic_reasons("echo /")


def test_other_catastrophic_patterns():
    assert catastrophic_reasons(":(){ :|:& };:")
    assert catastrophic_reasons("mkfs.ext4 /dev/disk2")
    assert catastrophic_reasons("dd if=/dev/zero of=/dev/disk0")
    assert catastrophic_reasons("echo x > /dev/sda")
    assert not catastrophic_reasons("echo hello")


def test_default_denylist_compiles_and_warns():
    patterns, errors = compile_denylist(DEFAULT_DENYLIST)
    assert errors == []
    assert patterns
    flagged = assess_instruction(_shell("sudo reboot"), patterns)
    assert flagged.level == "warn"
    plain = assess_instruction(_shell("ls -la"), patterns)
    assert plain.level == "ok"
    recursive = assess_instruction(_shell("rm -rf ./build"), patterns)
    assert recursive.level == "warn"


def test_interactive_and_partial_are_not_auto():
    patterns, _ = compile_denylist(DEFAULT_DENYLIST)
    interactive = assess_instruction(_shell("vim notes.txt"), patterns)
    assert interactive.level == "block"
    assert interactive.interactive
    python = assess_instruction(_shell("python3 -c 'print(1)'"), patterns)
    assert python.level == "ok"
    partial = assess_instruction(
        Instruction(op="shell", raw="ls", partial=True, ambiguous=True),
        patterns,
    )
    assert partial.level == "block"
    ambiguous = assess_instruction(_shell("ls", ambiguous=True), patterns)
    assert ambiguous.level == "warn"
    assert plan_approval("automatic", 0, 4, ambiguous) == "manual"


def test_plan_matrix():
    ok = SafetyAssessment("ok", [])
    warn = SafetyAssessment("warn", ["flagged"])
    blocked = SafetyAssessment("block", ["no"])
    ambiguous = SafetyAssessment("warn", ["fence"], ambiguous=True)
    assert plan_approval("manual", 0, 4, ok) == "manual"
    assert plan_approval("auto_n", 0, 4, ok) == "auto"
    assert plan_approval("auto_n", 3, 4, ok) == "auto"
    assert plan_approval("auto_n", 4, 4, ok) == "manual"
    assert plan_approval("automatic", 0, 4, ok) == "auto"
    assert plan_approval("automatic", 0, 4, warn) == "manual"
    assert plan_approval("auto_n", 0, 4, warn) == "manual"
    assert plan_approval("automatic", 9, 4, blocked) == "block"
    assert plan_approval("automatic", 0, 4, ambiguous) == "manual"


def test_review_never_upgrades():
    assert apply_review_to_plan("auto", "safe", True, False) == "auto"
    assert apply_review_to_plan("auto", "unsafe", True, False) == "manual"
    assert apply_review_to_plan("auto", "unsure", False, False) == "manual"
    assert apply_review_to_plan("auto", None, True, True) == "manual"
    assert apply_review_to_plan("block", "safe", True, False) == "block"
    assert apply_review_to_plan("manual", "safe", True, False) == "manual"
    assert parse_verdict("VERDICT: unsafe\nREASONS: deletes root") == "unsafe"
    assert parse_verdict("no verdict here") == "unsure"


def test_relay_plan_partial_stays_manual():
    assert plan_relay("automatic", 0, 4, partial=True) == "manual"
    assert plan_relay("automatic", 0, 4, partial=False) == "auto"
    assert plan_relay("auto_n", 4, 4, partial=False) == "manual"
    assert plan_relay("manual", 0, 4, partial=False) == "manual"
