from pathlib import Path
import pytest
from xueban_backend.passwords import hash_password, read_password_file, validate_password, validate_student_password, verify_password


def test_scrypt_random_salt_and_exact_whitespace_unicode():
    password = '  合成密码允许 空格 Unicode  '
    first, second = hash_password(password), hash_password(password)
    assert first != second
    assert verify_password(password, first)
    assert not verify_password(password.strip(), first)
    assert not verify_password('different synthetic password', first)
    assert not verify_password(password, first.replace('$131072$', '$9999999999$'))
    assert not verify_password(password, 'invalid')


@pytest.mark.parametrize('value', ['', 'a'*14, 'a'*129, None, '\ud800'*15])
def test_password_policy_fixed_boundaries(value):
    with pytest.raises(ValueError): validate_password(value)


@pytest.mark.parametrize('value', ['123', 'abcXYZ', 'a1b2c3', '9'*15])
def test_student_password_policy_accepts_letters_and_digits(value):
    validate_student_password(value)


@pytest.mark.parametrize('value', ['', 'ab', 'a'*16, 'abc-123', '中文密码'])
def test_student_password_policy_rejects_other_values(value):
    with pytest.raises(ValueError): validate_student_password(value)


def test_password_file_0600_no_follow_and_no_password_in_error(tmp_path):
    content = 'runtime synthetic password'
    path = tmp_path / 'password.txt'; path.write_text(content+'\n'); path.chmod(0o600)
    assert read_password_file(path) == content
    path.chmod(0o644)
    with pytest.raises(ValueError) as raised: read_password_file(path)
    assert content not in str(raised.value)
    path.chmod(0o600)
    link = tmp_path / 'linked'; link.symlink_to(path)
    with pytest.raises(OSError): read_password_file(link)
