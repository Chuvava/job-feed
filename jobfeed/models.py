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
    contracts: list[str] = field(default_factory=list)  # b2b | permanent | mandate_contract | any ...; empty = unknown
    published: str = ""          # YYYY-MM-DD, empty = unknown

    @property
    def no_b2b(self) -> bool:
        """Contract types are known and B2B isn't among them (the user works only on B2B)."""
        return no_b2b(self.contracts)


def no_b2b(contracts: list[str]) -> bool:
    return bool(contracts) and not {"b2b", "any"} & {c.lower() for c in contracts}
