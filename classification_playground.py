"""Hardcoded examples for prompt tuning outside the production inbox."""

from dataclasses import dataclass


@dataclass(frozen=True)
class PlaygroundComment:
    comment_id: str
    scenario: str
    text: str


PLAYGROUND_COMMENTS = (
    PlaygroundComment(
        "technical-question",
        "Technical question",
        "If the derivative term reacts to the rate of change, how do you stop "
        "sensor noise from making the controller unstable?",
    ),
    PlaygroundComment(
        "constructive-feedback",
        "Constructive feedback",
        "The explanation was clear, but showing the same tuning example with "
        "and without integral windup would make the trade-off easier to see.",
    ),
    PlaygroundComment(
        "content-idea",
        "Content idea",
        "Could you make a follow-up comparing a PID controller with model "
        "predictive control on the same robot?",
    ),
    PlaygroundComment(
        "personal-connection",
        "Personal connection",
        "This finally clicked for me. My dad taught control systems, and this "
        "video brought back the way he used to explain feedback loops.",
    ),
    PlaygroundComment(
        "quick-acknowledgement",
        "Quick acknowledgement",
        "Great video, thanks!",
    ),
    PlaygroundComment(
        "low-value",
        "Low value",
        "First.",
    ),
    PlaygroundComment(
        "spam",
        "Spam",
        "Amazing upload! Promote it on my channel and buy followers at "
        "best-growth.example.",
    ),
    PlaygroundComment(
        "needs-research",
        "Needs research",
        "Do modern passenger aircraft still use classical PID loops for flight "
        "control, or have they all moved to adaptive control?",
    ),
    PlaygroundComment(
        "incorrect-criticism",
        "Incorrect criticism",
        "This is wrong because the derivative term predicts the future error, "
        "so it can never amplify measurement noise.",
    ),
    PlaygroundComment(
        "discussion-starter",
        "Highly valuable discussion starter",
        "When a system is safe but not explainable, should engineers prefer a "
        "slightly worse controller whose failure modes humans can understand?",
    ),
)


def get_playground_comment(comment_id: str) -> PlaygroundComment | None:
    return next(
        (comment for comment in PLAYGROUND_COMMENTS if comment.comment_id == comment_id),
        None,
    )
