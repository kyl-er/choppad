"""export: export a chop bank to a zero-copy Ardour SFZ file and a Hydrogen drumkit directory.
See contracts/README.md §7 (v2.1)."""


def export_sfz(media_dir: str, out_path: str | None = None, *, bank: int = 0) -> str:
    """Write a zero-copy .sfz referencing audio.wav with sample-frame offset/end, loop_mode=one_shot,
    group=1, off_by=1, ampeg_attack=0.002, and <region> key=36+pad for each chop in banks[bank].
    Returns the written .sfz path."""
    raise NotImplementedError("export.export_sfz")


def export_hydrogen(media_dir: str, out_dir: str, *, bank: int = 0, kit_name: str | None = None) -> str:
    """Slice audio.wav for each pad in banks[bank] into out_dir/chop_00.wav..chop_NN.wav (s16le 44.1k stereo
    with 2.5 ms fade-in and 3 ms fade-out) and write out_dir/drumkit.xml (muteGroup=0, midiOutNote=36+pad).
    Returns the written drumkit.xml path."""
    raise NotImplementedError("export.export_hydrogen")
