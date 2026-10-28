from pathlib import Path
import pytest
from xueban_backend.passwords import (
    hash_password,
    read_password_file,
    validate_login_password,
    validate_password,
    validate_student_password,
    verify_password,
)


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


@pytest.mark.parametrize('value', ['abc', 'ABC', '123', 'aB3', 'a' * 15, '123456789012345'])
def test_student_password_accepts_ascii_letters_or_digits_at_boundaries(value):
    validate_student_password(value)


@pytest.mark.parametrize('value', [
    '', 'ab', 'a' * 16, 'abc!', 'ab_c', 'ab-c', 'ab c', ' abc', 'abc ',
    'abc\n', 'ab\tc', '学123', 'é12', '１２３', '\ud800ab', None, 123, b'abc',
])
def test_student_password_rejects_invalid_length_characters_and_types(value):
    with pytest.raises(ValueError):
        validate_student_password(value)


@pytest.mark.parametrize('value', ['abc', '123', '中文密', ' a ', 'a' * 128, '😀' * 128])
def test_login_password_accepts_short_and_legacy_unicode_boundaries(value):
    validate_login_password(value)


@pytest.mark.parametrize('value', ['', 'ab', 'a' * 129, '😀' * 129, '\ud800ab', None, 123, b'abc'])
def test_login_password_rejects_invalid_length_encoding_and_types(value):
    with pytest.raises(ValueError):
        validate_login_password(value)


def test_hash_password_uses_explicit_student_policy_without_weakening_default():
    with pytest.raises(ValueError):
        hash_password('aB3')
    with pytest.raises(ValueError):
        hash_password('abc!', validator=validate_student_password)
    encoded = hash_password('aB3', validator=validate_student_password)
    assert verify_password('aB3', encoded)
    assert not verify_password('ab3', encoded)
    assert not verify_password(' aB3 ', encoded)


def test_hash_password_login_policy_preserves_unicode_and_whitespace():
    password = ' 密 '
    encoded = hash_password(password, validator=validate_login_password)
    assert verify_password(password, encoded)
    assert not verify_password(password.strip(), encoded)


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
