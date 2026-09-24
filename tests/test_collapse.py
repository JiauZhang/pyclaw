"""The collapse pass is pure: a Group accumulates collapsible tool uses,
deduplicates reads by path, and renders the running/finished summary."""
from pyclaw.tui.collapse import Group, group_text, recent_rollup


def test_reads_deduplicate_by_key_and_other_kinds_do_not():
    group = Group()
    group.add({'read'}, 'a.py', 'u1')
    group.add({'read'}, 'a.py', 'u2')
    group.add({'read'}, 'b.py', 'u3')
    group.add({'search'}, 'x', 'u4')
    group.add({'search'}, 'y', 'u5')
    assert group.counts == {'search': 2, 'read': 2, 'list': 0, 'bash': 0,
                            'memory_read': 0, 'memory_write': 0}


def test_every_entry_is_recorded_even_when_deduplicated():
    group = Group()
    group.add({'read'}, 'a.py', 'u1')
    group.add({'read'}, 'a.py', 'u2')
    assert group.entries == [({'read'}, 'a.py', 'u1'), ({'read'}, 'a.py', 'u2')]


def test_a_memory_write_is_counted_its_own_kind():
    group = Group()
    group.add({'memory_write'}, 'AGENTS.md', 'u1')
    assert group.counts['memory_write'] == 1
    assert 'Saving 1 memory' in group_text(group.counts, active=True,
                                           markup=False)


def test_the_summary_speaks_present_while_running_and_past_when_done():
    group = Group()
    group.add({'search'}, 'x', 'u1')
    group.add({'read'}, 'a.py', 'u2')
    assert group.summary() == ('Looking for [bold]1[/] pattern, '
                                'opening [bold]1[/] file')
    group.active = False
    assert group.summary() == ('Looked for [bold]1[/] pattern, '
                                'opened [bold]1[/] file')


def test_group_text_marks_the_first_verb_and_lowers_the_rest():
    text = group_text({'read': 2, 'list': 1}, active=False, markup=False)
    assert text == 'Opened 2 files, walked 1 folder'
    assert group_text({}, active=True) == ''


def test_recent_rollup_counts_the_trailing_run_of_collapsible_work():
    assert recent_rollup([{'bash'}, {'read'}, {'search'}, {'read'}]) == \
        'Looking for 1 pattern, opening 2 files'
    assert recent_rollup([{'bash'}, {'read'}, {'search'}]) == \
        'Looking for 1 pattern, opening 1 file'


def test_a_short_trailing_run_is_not_worth_a_rollup():
    assert recent_rollup([{'read'}]) == ''
    assert recent_rollup([]) == ''
