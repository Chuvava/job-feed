from dataclasses import dataclass, field


@dataclass
class Job:
    id: str                      # "<source>:<native id>", used for dedupe
    source: str                  # justjoin | nofluff | linkedin
    title: str
    company: str
    url: str
    location: str = ""
    workplace: str = ""          # remote | hybrid | office | ""
    salary: str = ""
    seniority: str = ""
    skills: list[str] = field(default_factory=list)
