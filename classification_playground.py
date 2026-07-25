"""Hardcoded regression examples for prompt tuning outside production."""

from dataclasses import dataclass

from classification_service import CommentClassification


@dataclass(frozen=True)
class PlaygroundComment:
    comment_id: str
    scenario: str
    text: str
    video_title: str
    expected_categories: tuple[str, ...]
    priority_min: float
    priority_max: float
    expected_reply_worthy: bool
    expected_needs_research: bool

    @property
    def expected_intent(self) -> str:
        categories = " or ".join(self.expected_categories)
        return (
            f"{categories}; approximately {self.priority_min:.2f}-"
            f"{self.priority_max:.2f}, "
            f"{'reply-worthy' if self.expected_reply_worthy else 'not reply-worthy'}, "
            f"with needs_research {str(self.expected_needs_research).lower()}."
        )

    def is_broadly_consistent(self, result: CommentClassification) -> bool:
        return (
            result.category in self.expected_categories
            and self.priority_min <= result.priority <= self.priority_max
            and result.reply_worthy == self.expected_reply_worthy
            and result.needs_research == self.expected_needs_research
        )


PLAYGROUND_COMMENTS = (
    PlaygroundComment("generic-praise", "Generic praise", "Nice video!", "PID Explained in 60 seconds", ("generic_praise",), 0.20, 0.40, True, False),
    PlaygroundComment("heartfelt-thanks", "Heartfelt thanks", "I've struggled with control theory for months, and your explanation was the first one that made it click. Thank you for taking the time.", "PID Explained in 60 seconds", ("community_connection", "specific_appreciation"), 0.85, 1.00, True, False),
    PlaygroundComment("inspired-engineer", "Inspired to start engineering", "Your robot series inspired me to apply for an engineering course. I start next month and just wanted you to know these videos mattered.", "Building My First Robot", ("community_connection",), 0.90, 1.00, True, False),
    PlaygroundComment("returning-viewer", "Returning viewer", "I've been here since the first balancing-robot video. It has been great watching both the project and your explanations improve each week.", "Balancing Robot Update", ("community_connection",), 0.70, 0.89, True, False),
    PlaygroundComment("technical-correction", "Technical correction", "At 4:12 the diagram labels the derivative gain as Ki, but it should be Kd. The explanation itself sounds correct.", "PID Explained in 60 seconds", ("constructive_correction",), 0.90, 1.00, True, False),
    PlaygroundComment("technical-question", "Technical question", "If the derivative term reacts to the rate of change, how do you stop sensor noise from making the controller unstable?", "PID Explained in 60 seconds", ("technical_question",), 0.80, 0.95, True, False),
    PlaygroundComment("content-idea", "Content idea", "Could you make a follow-up comparing a PID controller with model predictive control on the same robot?", "PID Explained in 60 seconds", ("content_idea",), 0.70, 0.89, True, False),
    PlaygroundComment("spam", "Spam", "Amazing upload! Promote it on my channel and buy followers at best-growth.example.", "PID Explained in 60 seconds", ("spam",), 0.00, 0.09, False, False),
    PlaygroundComment("first", "Empty first comment", "First.", "PID Explained in 60 seconds", ("low_value", "spam"), 0.00, 0.09, False, False),
    PlaygroundComment("shared-experience", "Shared engineering experience", "We used a similar controller on our student rover and learned the hard way that wheel slip made the encoder data misleading. Your filtering section would have saved us a week.", "PID Explained in 60 seconds", ("community_connection", "meaningful_discussion"), 0.75, 0.90, True, False),
    PlaygroundComment("specific-appreciation", "Specific appreciation", "So nice to see good code and design philosophy in shorts for once", "Your Button Is Lying to Your Microcontroller...", ("specific_appreciation",), 0.75, 0.90, True, False),
    PlaygroundComment("humorous-engagement", "Humorous engineering engagement", "The missile knows where it is not therefore it knows where it is. It computes the distance from where it once was to where it is, and computes the difference between where it is and where it isn’t, and thus determines where it should go. Or something like that", "PID Explained in 60 seconds", ("humorous_engagement",), 0.40, 0.60, True, False),
)


def get_playground_comment(comment_id: str) -> PlaygroundComment | None:
    return next(
        (comment for comment in PLAYGROUND_COMMENTS if comment.comment_id == comment_id),
        None,
    )
