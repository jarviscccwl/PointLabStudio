"""Explicit numeric text-column mapping and read-only cloud import regressions."""
import os
os.environ.setdefault('QT_QPA_PLATFORM', 'offscreen')

import numpy as np
import pytest

from core.cancellation import OperationCancelled


def backend():
    from core.io.text_cloud import preview_text_cloud, read_text_cloud
    return preview_text_cloud, read_text_cloud


def test_named_csv_preserves_extra_numeric_fields(tmp_path):
    path = tmp_path / 'points.csv'
    original = 'x,y,z,intensity,label\n1,2,3,40,7\n4,5,6,50,8\n'
    path.write_text(original, encoding='utf-8')
    preview, read = backend()
    sample = preview(str(path))
    assert sample.delimiter == ',' and sample.columns == ['x', 'y', 'z', 'intensity', 'label']
    assert sample.skip_rows == 1 and len(sample.rows) == 2
    options = dict(delimiter=sample.delimiter, skip_rows=sample.skip_rows,
                   columns=sample.columns, x=0, y=1, z=2)
    cloud = read(str(path), options)
    np.testing.assert_allclose(cloud.xyz, [[1, 2, 3], [4, 5, 6]])
    np.testing.assert_array_equal(cloud.field('label'), [7, 8])
    np.testing.assert_array_equal(cloud.field('intensity'), [40, 50])
    assert cloud.path == str(path) and path.read_text(encoding='utf-8') == original


def test_columns_comment_header_and_whitespace_mapping(tmp_path):
    path = tmp_path / 'data.txt'
    path.write_text('# exported cloud\n# columns: reflectivity Z X Y\n9 30 10 20\n', encoding='utf-8')
    preview, read = backend()
    sample = preview(str(path))
    assert sample.columns == ['reflectivity', 'Z', 'X', 'Y']
    assert sample.delimiter is None and sample.skip_rows == 0
    cloud = read(str(path), dict(delimiter=None, columns=sample.columns, x=2, y=3, z=1))
    np.testing.assert_allclose(cloud.xyz, [[10, 20, 30]])
    assert cloud.field('reflectivity').tolist() == [9]


def test_headerless_preview_is_explicit_and_bounded(tmp_path):
    path = tmp_path / 'many.tsv'
    path.write_text('1\t2\t3\t4\n' * 100000, encoding='utf-8')
    preview, read = backend()
    sample = preview(str(path))
    assert sample.columns == ['Column 1', 'Column 2', 'Column 3', 'Column 4']
    assert not sample.has_header and len(sample.rows) <= 30 and sample.sample_bytes <= 65536
    with pytest.raises(ValueError, match='X|mapping|映射'):
        read(str(path), dict(delimiter='\t', columns=sample.columns))


@pytest.mark.parametrize('row', ['1,2,3\n4,5\n', '1,2,3\n4,oops,6\n', '1,2,3\n4,nan,6\n'])
def test_bad_numeric_rows_report_line_without_silent_skips(tmp_path, row):
    path = tmp_path / 'bad.csv'
    path.write_text(row, encoding='utf-8')
    _, read = backend()
    with pytest.raises(ValueError, match='2'):
        read(str(path), dict(delimiter=',', columns=['x', 'y', 'z'], x=0, y=1, z=2))


def test_text_import_cancellation(tmp_path):
    path = tmp_path / 'cancel.txt'
    path.write_text('1 2 3\n' * 5000, encoding='utf-8')
    _, read = backend()
    calls = [0]
    def cancel():
        calls[0] += 1
        return calls[0] > 2
    with pytest.raises(OperationCancelled):
        read(str(path), dict(delimiter=None, x=0, y=1, z=2), cancel=cancel)


def test_preview_delimiter_and_header_override(tmp_path):
    path = tmp_path / 'semicolon.csv'
    path.write_text('a;b;c\n1;2;3\n', encoding='utf-8')
    preview, _ = backend()
    sample = preview(str(path), delimiter=';', header=True)
    assert sample.columns == ['a', 'b', 'c'] and sample.skip_rows == 1


def test_dialog_named_mapping_and_headerless_confirmation(tmp_path):
    from PySide6.QtWidgets import QApplication, QDialogButtonBox
    from ui.widgets.text_cloud_import import TextCloudImportDialog
    _app = QApplication.instance() or QApplication([])
    path = tmp_path / 'mapped.csv'
    path.write_text('z,x,y,quality\n30,10,20,9\n', encoding='utf-8')
    dialog = TextCloudImportDialog(str(path))
    assert [dialog.options()[axis] for axis in ['x', 'y', 'z']] == [1, 2, 0]
    assert dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
    dialog.close()
    path.write_text('1 2 3\n4 5 6\n', encoding='utf-8')
    dialog = TextCloudImportDialog(str(path))
    assert not dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
    for axis, index in [('x', 0), ('y', 1), ('z', 2)]:
        combo = dialog.axis_combos[axis]
        combo.setCurrentIndex(combo.findData(index))
    assert dialog.buttons.button(QDialogButtonBox.Ok).isEnabled()
    cloud = backend()[1](str(path), dialog.options())
    assert cloud.point_count == 2
    dialog.close()


def test_full_import_exceeds_preview_and_preserves_duplicate_fields(tmp_path):
    path = tmp_path / 'full.csv'
    path.write_text('x,y,z,value,value\n' + '1,2,3,4,5\n' * 9000, encoding='utf-8')
    preview, read = backend()
    sample = preview(str(path))
    assert len(sample.rows) == 30
    assert sample.columns[-2:] == ['value', 'value_2']
    cloud = read(str(path), dict(delimiter=',', skip_rows=sample.skip_rows,
                                columns=sample.columns, x=0, y=1, z=2))
    assert cloud.point_count == 9000
    assert cloud.field('value')[-1] == 4 and cloud.field('value_2')[-1] == 5


def test_repeated_axis_mapping_is_rejected(tmp_path):
    path = tmp_path / 'coordinates.txt'
    path.write_text('1 2 3\n', encoding='utf-8')
    with pytest.raises(ValueError, match='X|mapping|映射'):
        backend()[1](str(path), dict(delimiter=None, x=0, y=0, z=2))
