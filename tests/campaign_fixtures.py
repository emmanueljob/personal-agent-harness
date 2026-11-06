"""Synthetic campaigns used only by tests; never application seed data."""
from harness.personalization import Profile
from harness.site_config import save


def seed_campaigns():
    for prefix, labels in [('source',['agency','warm','cold']),('content',['alex','casey']),('campaign',['openai','anthropic','exampleco','sierra','sampleco'])]:
        for label in labels:
            p=Profile(key=f'{prefix}:{label}',headline=f"Hi {label.title()} — a sample introduction",pitch='Synthetic product experience.',featured=['sample'],
                      questions=['why him for my role?', 'What was the sample project?', 'How does the team work?'],
                      system_note='AI analytics product work',cta='Book 15 min',company=label if prefix=='campaign' else '',
                      first90=['Sample opportunity','Sample delivery','Sample outcome'] if prefix=='campaign' else [])
            save('profile:'+p.key,p.model_dump(),0)
