"""Validated HTTPS destinations and offline PSL/IDNA registrable domains."""
import ipaddress
from urllib.parse import urlsplit, unquote
import idna
import tldextract
from django.core.exceptions import ValidationError
from django.core.validators import validate_email

_extract = tldextract.TLDExtract(suffix_list_urls=(), cache_dir=None, include_psl_private_domains=True)
FREE_EMAIL = {'gmail.com','googlemail.com','outlook.com','hotmail.com','live.com','yahoo.com','yahoo.co.uk','icloud.com','aol.com','proton.me','protonmail.com'}
SHORTENERS = {'bit.ly','tinyurl.com','t.co','is.gd','ow.ly','buff.ly','shorturl.at','rb.gy'}


def host_domain(host):
    try:
        host = idna.encode(host.rstrip('.'), uts46=True, std3_rules=True).decode('ascii').lower()
        try:
            ipaddress.ip_address(host)
        except ValueError:
            pass
        else:
            raise ValidationError('Use a public organization domain, not an IP address.')
        result = _extract(host)
        if not result.suffix or not result.domain:
            raise ValidationError('Use a valid public organization domain.')
        return result.top_domain_under_public_suffix
    except (idna.IDNAError, UnicodeError, AttributeError) as exc:
        raise ValidationError('Enter a valid domain.') from exc


def website_domain(value):
    value = str(value or '').strip()
    if not value:
        return ''
    try:
        if any(ord(c)<33 or ord(c)==127 for c in value) or '\\' in value:
            raise ValueError
        decoded = unquote(value)
        if any(ord(c)<32 for c in decoded) or '\\' in decoded:
            raise ValueError
        parts = urlsplit(value)
        if parts.scheme.lower() != 'https' or not parts.hostname or parts.username is not None or parts.password is not None:
            raise ValueError
        if parts.port is not None and not 1 <= parts.port <= 65535:
            raise ValueError
        return host_domain(parts.hostname)
    except ValueError as exc:
        raise ValidationError('Use a well-formed HTTPS URL without embedded credentials.') from exc


def email_domain(value):
    value = str(value or '').strip()
    if not value:
        return ''
    validate_email(value)
    return host_domain(value.rsplit('@',1)[1])
