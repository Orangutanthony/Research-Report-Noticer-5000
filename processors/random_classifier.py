import random

from processors import Classification


def classify(doc):
    return Classification(is_report=random.choice((True, False)), notes=("random classifier, verdict is a coin flip",))
