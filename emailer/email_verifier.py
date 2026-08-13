"""
email_verifier.py

Standalone email verification module for Lead Scout.
Matches existing conventions in emailer/smtp_verifier.py:
  - stdlib only except dnspython for MX lookups
  - ASCII-only console output (no unicode arrows/checks)
  - returns structured status strings usable in the CSV email_status column

Install dependency:
    pip install dnspython

Usage:
    from email_verifier import verify_email

    result = verify_email("jsmith@chordenergy.com")
    print(result)
    # {'email': 'jsmith@chordenergy.com', 'syntax_valid': True,
    #  'mx_found': True, 'mx_host': 'aspmx.l.google.com',
    #  'smtp_status': 'accepted', 'catch_all': False,
    #  'status': 'verified'}
"""

import re
import smtplib
import socket
import random
import string
import time

try:
    import dns.resolver
except ImportError:
    raise ImportError("dnspython is required. Run: pip install dnspython")


EMAIL_REGEX = re.compile(
    r"^[a-zA-Z0-9._%+\-]+@[a-zA-Z0-9.\-]+\.[a-zA-Z]{2,}$"
)

# Common disposable/temp email domains worth flagging.
# Not exhaustive, extend as needed.
DISPOSABLE_DOMAINS = {
    "mailinator.com", "guerrillamail.com", "10minutemail.com",
    "tempmail.com", "throwawaymail.com", "yopmail.com",
    "trashmail.com", "getnada.com", "fakeinbox.com",
}

# Generic role-based local parts, useful to flag separately from
# individual contact addresses in a sales context.
ROLE_LOCALPARTS = {
    "info", "admin", "support", "sales", "contact", "hello",
    "office", "help", "webmaster", "noreply", "no-reply",
}

DEFAULT_TIMEOUT = 10
DEFAULT_HELO_DOMAIN = "leadscout.test"
DEFAULT_MAIL_FROM = "probe@leadscout.test"


def check_syntax(email):
    return bool(EMAIL_REGEX.match(email))


def get_domain(email):
    return email.split("@", 1)[1].lower()


def get_mx_records(domain, timeout=DEFAULT_TIMEOUT):
    """
    Returns a list of (priority, host) tuples sorted by priority,
    or an empty list if no MX records exist.
    """
    try:
        resolver = dns.resolver.Resolver()
        resolver.timeout = timeout
        resolver.lifetime = timeout
        answers = resolver.resolve(domain, "MX")
        records = sorted(
            [(r.preference, str(r.exchange).rstrip(".")) for r in answers],
            key=lambda x: x[0],
        )
        return records
    except (dns.resolver.NXDOMAIN, dns.resolver.NoAnswer):
        return []
    except Exception:
        # Timeout, SERVFAIL, or other resolver error
        return []


def _random_localpart(length=20):
    return "".join(random.choices(string.ascii_lowercase + string.digits, k=length))


def smtp_probe(email, mx_host, helo_domain=DEFAULT_HELO_DOMAIN,
               mail_from=DEFAULT_MAIL_FROM, timeout=DEFAULT_TIMEOUT):
    """
    Connects to mx_host and issues a RCPT TO probe without sending mail.
    Returns one of: 'accepted', 'rejected', 'unknown'

    Known limitation (same as smtp_verifier.py): most residential and
    office ISPs block outbound port 25 or are listed on Spamhaus PBL,
    which makes mail servers reject the probe outright regardless of
    whether the mailbox is real. If every probe you run returns
    'unknown' or 'rejected' including for addresses you know are valid,
    that is very likely what is happening, not a bug in this code.
    """
    try:
        server = smtplib.SMTP(timeout=timeout)
        server.connect(mx_host, 25)
        server.helo(helo_domain)
        server.mail(mail_from)
        code, _ = server.rcpt(email)
        server.quit()

        if code == 250:
            return "accepted"
        elif code in (550, 551, 553, 554):
            return "rejected"
        else:
            return "unknown"
    except (socket.timeout, socket.error, smtplib.SMTPException):
        return "unknown"


def check_catch_all(domain, mx_host, helo_domain=DEFAULT_HELO_DOMAIN,
                     mail_from=DEFAULT_MAIL_FROM, timeout=DEFAULT_TIMEOUT):
    """
    Probes a random, almost-certainly-nonexistent address at the domain.
    If the server accepts it, the domain is catch-all (accepts anything),
    which means an 'accepted' result on the real address is not reliable
    proof that mailbox exists.
    """
    fake_local = _random_localpart()
    fake_email = f"{fake_local}@{domain}"
    result = smtp_probe(fake_email, mx_host, helo_domain, mail_from, timeout)
    return result == "accepted"


def verify_email(email, helo_domain=DEFAULT_HELO_DOMAIN,
                  mail_from=DEFAULT_MAIL_FROM, timeout=DEFAULT_TIMEOUT,
                  run_smtp=True):
    """
    Full verification pipeline. Returns a dict suitable for merging
    into a CSV row.

    Set run_smtp=False to skip the SMTP probe step entirely and only
    do syntax + MX + disposable/role checks. Useful on connections
    where port 25 is blocked, since the SMTP step will just burn time
    returning 'unknown' for everything.
    """
    result = {
        "email": email,
        "syntax_valid": False,
        "disposable": False,
        "role_account": False,
        "mx_found": False,
        "mx_host": None,
        "smtp_status": "not_checked",
        "catch_all": False,
        "status": "error",
    }

    if not check_syntax(email):
        result["status"] = "invalid_syntax"
        return result

    result["syntax_valid"] = True
    local_part, domain = email.split("@", 1)
    domain = domain.lower()
    local_part = local_part.lower()

    result["disposable"] = domain in DISPOSABLE_DOMAINS
    result["role_account"] = local_part in ROLE_LOCALPARTS

    if result["disposable"]:
        result["status"] = "disposable"
        return result

    mx_records = get_mx_records(domain, timeout=timeout)
    if not mx_records:
        result["status"] = "no_mx"
        return result

    result["mx_found"] = True
    mx_host = mx_records[0][1]
    result["mx_host"] = mx_host

    if not run_smtp:
        result["status"] = "mx_only"
        return result

    smtp_result = smtp_probe(email, mx_host, helo_domain, mail_from, timeout)
    result["smtp_status"] = smtp_result

    if smtp_result == "accepted":
        is_catch_all = check_catch_all(domain, mx_host, helo_domain, mail_from, timeout)
        result["catch_all"] = is_catch_all
        result["status"] = "catch-all-risky" if is_catch_all else "verified"
    elif smtp_result == "rejected":
        result["status"] = "bounced"
    else:
        result["status"] = "unknown"

    return result


def verify_batch(emails, delay_seconds=1.0, run_smtp=True, timeout=DEFAULT_TIMEOUT):
    """
    Verifies a list of emails with a delay between SMTP probes to
    avoid tripping rate limits or greylisting on the receiving server.
    Yields results one at a time so callers can write progress to a
    CSV incrementally rather than waiting on the whole batch.
    """
    for email in emails:
        yield verify_email(email, run_smtp=run_smtp, timeout=timeout)
        if run_smtp:
            time.sleep(delay_seconds)


if __name__ == "__main__":
    import sys

    if len(sys.argv) != 2:
        print("Usage: python email_verifier.py someone@example.com")
        sys.exit(1)

    test_email = sys.argv[1]
    print(f"Verifying: {test_email}")
    r = verify_email(test_email)
    for key, value in r.items():
        print(f"  {key}: {value}")
