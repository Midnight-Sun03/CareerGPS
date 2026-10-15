import re
import time

from django.contrib.auth.models import User
from django.core import mail
from django.test import TestCase, override_settings
from django.urls import reverse

from core.models import Profile


@override_settings(
    EMAIL_BACKEND=(
        "django.core.mail.backends.locmem.EmailBackend"
    ),
    EMAIL_HOST="smtp.test",
    DEFAULT_FROM_EMAIL=(
        "CareerGPS <accounts@example.test>"
    ),
)
class RegistrationOTPTests(TestCase):

    def registration_data(
        self,
        *,
        email="candidate@example.com",
    ):
        return {
            "first_name": "Test",
            "last_name": "Candidate",
            "email": email,
            "password": "CareerGPS!Test2026",
            "confirm_password": (
                "CareerGPS!Test2026"
            ),
            "terms_agreed": "on",
            "privacy_acknowledged": "on",
        }

    def extract_otp(self):
        self.assertEqual(
            len(mail.outbox),
            1,
        )

        match = re.search(
            r"\b(\d{6})\b",
            mail.outbox[0].body,
        )

        self.assertIsNotNone(
            match
        )

        return match.group(1)

    def test_registration_sends_real_otp_email(self):
        response = self.client.post(
            reverse("register"),
            self.registration_data(),
        )

        self.assertRedirects(
            response,
            reverse("verify_otp"),
            fetch_redirect_response=False,
        )

        user = User.objects.get(
            email="candidate@example.com"
        )

        self.assertFalse(
            user.is_active
        )

        self.assertTrue(
            Profile.objects.filter(
                user=user
            ).exists()
        )

        self.assertEqual(
            len(mail.outbox),
            1,
        )

        self.assertEqual(
            mail.outbox[0].to,
            [
                "candidate@example.com"
            ],
        )

        session = self.client.session

        # OTP itself must not be stored in plaintext.
        self.assertNotIn(
            "otp",
            session,
        )

        self.assertIn(
            "otp_hash",
            session,
        )

        self.assertIn(
            "otp_expires_at",
            session,
        )

    def test_correct_otp_activates_user(self):
        self.client.post(
            reverse("register"),
            self.registration_data(),
        )

        otp = self.extract_otp()

        response = self.client.post(
            reverse("verify_otp"),
            {
                "otp": otp
            },
        )

        user = User.objects.get(
            email="candidate@example.com"
        )

        user.refresh_from_db()

        self.assertTrue(
            user.is_active
        )

        self.assertRedirects(
            response,
            reverse("dashboard"),
            fetch_redirect_response=False,
        )

        session = self.client.session

        self.assertNotIn(
            "otp_hash",
            session,
        )

        self.assertIn(
            "user_id",
            session,
        )

    def test_used_otp_cannot_activate_user_again(self):
        self.client.post(
            reverse("register"),
            self.registration_data(),
        )

        otp = self.extract_otp()

        # First use - should activate and clear OTP
        response = self.client.post(
            reverse("verify_otp"),
            {"otp": otp},
        )

        self.assertRedirects(response, reverse("dashboard"))

        user = User.objects.get(email="candidate@example.com")
        self.assertTrue(user.is_active)

        session = self.client.session
        self.assertNotIn("otp_hash", session)

        # Second use - same OTP should fail with "no active OTP"
        response = self.client.post(
            reverse("verify_otp"),
            {"otp": otp},
        )

        self.assertEqual(response.status_code, 200)
        self.assertContains(
            response,
            "no active verification code",
        )
        self.assertFalse(
            User.objects.get(email="candidate@example.com").is_active
        )

    def test_wrong_otp_increments_attempt_count(self):
        self.client.post(
            reverse("register"),
            self.registration_data(),
        )

        self.client.post(
            reverse("verify_otp"),
            {
                "otp": "000000"
            },
        )

        self.assertEqual(
            self.client.session[
                "otp_attempts"
            ],
            1,
        )

        user = User.objects.get(
            email="candidate@example.com"
        )

        self.assertFalse(
            user.is_active
        )

    def test_expired_otp_does_not_activate_user(self):
        self.client.post(
            reverse("register"),
            self.registration_data(),
        )

        otp = self.extract_otp()

        session = self.client.session

        session[
            "otp_expires_at"
        ] = (
            int(time.time())
            - 1
        )

        session.save()

        self.client.post(
            reverse("verify_otp"),
            {
                "otp": otp
            },
        )

        user = User.objects.get(
            email="candidate@example.com"
        )

        self.assertFalse(
            user.is_active
        )

        self.assertNotIn(
            "otp_hash",
            self.client.session,
        )

    def test_active_duplicate_email_is_rejected(self):
        User.objects.create_user(
            username=(
                "candidate@example.com"
            ),
            email=(
                "candidate@example.com"
            ),
            password="ExistingPassword123!",
            is_active=True,
        )

        response = self.client.post(
            reverse("register"),
            self.registration_data(),
        )

        self.assertEqual(
            response.status_code,
            200,
        )

        self.assertContains(
            response,
            (
                "An account with this email "
                "already exists."
            ),
        )

        self.assertEqual(
            len(mail.outbox),
            0,
        )

    def test_unverified_account_can_restart_verification(self):
        user = User.objects.create_user(
            username=(
                "candidate@example.com"
            ),
            email=(
                "candidate@example.com"
            ),
            password="OldPassword123!",
            is_active=False,
        )

        Profile.objects.create(
            user=user
        )

        original_user_id = user.id

        response = self.client.post(
            reverse("register"),
            self.registration_data(),
        )

        self.assertRedirects(
            response,
            reverse("verify_otp"),
            fetch_redirect_response=False,
        )

        self.assertEqual(
            User.objects.filter(
                email__iexact=(
                    "candidate@example.com"
                )
            ).count(),
            1,
        )

        user.refresh_from_db()

        self.assertEqual(
            user.id,
            original_user_id,
        )

        self.assertFalse(
            user.is_active
        )

        self.assertTrue(
            user.check_password(
                "CareerGPS!Test2026"
            )
        )

        self.assertEqual(
            len(mail.outbox),
            1,
        )

    def test_immediate_resend_is_rate_limited(self):
        self.client.post(
            reverse("register"),
            self.registration_data(),
        )

        self.assertEqual(
            len(mail.outbox),
            1,
        )

        response = self.client.post(
            reverse("resend_otp")
        )

        self.assertRedirects(
            response,
            reverse("verify_otp"),
            fetch_redirect_response=False,
        )

        # No second message inside the cooldown.
        self.assertEqual(
            len(mail.outbox),
            1,
        )
