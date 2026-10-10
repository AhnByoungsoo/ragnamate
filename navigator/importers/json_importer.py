"""Guide Pack JSON Import/Export (GuideRepository 위임)."""


def import_json(repo, path):
    return repo.import_pack(path)


def export_json(repo, path, guide_ids=None, name='exported'):
    return repo.export_pack(path, guide_ids, name)
