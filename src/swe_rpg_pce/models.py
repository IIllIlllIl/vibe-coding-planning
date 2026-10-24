"""RPG uses the existing repository Case envelope and explicit safe projection."""
from src.swe_verified_pce.models import SWEVerifiedPCECase


class SWERPGPCECase(SWEVerifiedPCECase):
    @property
    def working_directory(self):
        return self.source_row["working_dir"]

    def agent_projection(self):
        value = super().agent_projection()
        value["repository"].update(dataset_type="swe_rpg", working_directory=self.working_directory)
        return value

    def evaluator_input(self):
        return dict(self.source_row)
