"""Employer-specific Greenhouse interpretation.

The generic Greenhouse adapter preserves API facts without assigning employer
semantics to application configuration.  Any verified employer-specific
mapping belongs here and is merged only after the generic facts are captured.
"""


def interpret_greenhouse_employer(board_token, detail, content):
    # Takealot currently exposes the complete advert through ``content`` but no
    # separately verified structured experience/qualification fields.  Keep
    # the content intact and do not infer structured requirements from it.
    if str(board_token or "").lower() in {"takealot", "takealotgroup"}:
        return {
            "source_specific_evidence": {
                "employer_interpreter": "takealot",
                "content_fidelity": "full_greenhouse_advert",
            }
        }
    return {}
