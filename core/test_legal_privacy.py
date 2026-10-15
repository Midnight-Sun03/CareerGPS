from django.contrib.auth.models import User
from django.core import mail
from django.core.checks import Warning
from django.test import TestCase, override_settings
from django.urls import reverse

from .legal import current_document
from .checks import public_operator_details
from .models import LegalAcceptanceEvent, PrivacyRequest, Profile


@override_settings(EMAIL_BACKEND='django.core.mail.backends.locmem.EmailBackend',
                   EMAIL_HOST='smtp.test')
class LegalRegistrationTests(TestCase):
    def data(self):
        return {
            'first_name': 'Sam', 'last_name': 'Example',
            'email': 'sam@example.test', 'password': 'StrongCareerGPS2026!',
            'confirm_password': 'StrongCareerGPS2026!',
            'terms_agreed': 'on', 'privacy_acknowledged': 'on',
        }

    def test_both_independent_controls_are_required_server_side(self):
        for missing in ('terms_agreed', 'privacy_acknowledged'):
            with self.subTest(missing=missing):
                data = self.data()
                data.pop(missing)
                response = self.client.post(reverse('register'), data)
                self.assertEqual(response.status_code, 200)
                self.assertContains(response,
                                    'Agree to the Terms' if missing == 'terms_agreed'
                                    else 'Acknowledge the Privacy Notice')
                self.assertFalse(User.objects.filter(email='sam@example.test').exists())
                self.assertEqual(LegalAcceptanceEvent.objects.count(), 0)
                self.assertEqual(len(mail.outbox), 0)

        data = self.data()
        data.pop('terms_agreed')
        data.pop('privacy_acknowledged')
        response = self.client.post(reverse('register'), data)
        self.assertContains(response, 'Agree to the Terms')
        self.assertContains(response, 'Acknowledge the Privacy Notice')
        self.assertFalse(User.objects.filter(email='sam@example.test').exists())

    def test_registration_records_distinct_durable_events_once(self):
        self.assertRedirects(self.client.post(reverse('register'), self.data()),
                             reverse('verify_otp'), fetch_redirect_response=False)
        user = User.objects.get(email='sam@example.test')
        events = list(LegalAcceptanceEvent.objects.filter(user=user))
        self.assertEqual(len(events), 2)
        self.assertEqual({(event.document_type, event.action) for event in events},
                         {('terms', 'agreed'), ('privacy', 'acknowledged')})
        for event in events:
            document = current_document(event.document_type)
            self.assertEqual(event.document_version, document.version)
            self.assertEqual(event.content_sha256, document.content_sha256)
        self.client.post(reverse('register'), self.data())
        self.assertEqual(LegalAcceptanceEvent.objects.filter(user=user).count(), 2)
        self.assertEqual(len(mail.outbox), 2)

    def test_new_document_version_retains_historical_event(self):
        self.client.post(reverse('register'), self.data())
        user = User.objects.get(email='sam@example.test')
        with override_settings(LEGAL_TERMS_VERSION='2026-10-01.1'):
            self.client.post(reverse('register'), self.data())
            new_hash = current_document('terms').content_sha256
        terms = list(LegalAcceptanceEvent.objects.filter(user=user, document_type='terms'))
        self.assertEqual(len(terms), 2)
        self.assertIn(new_hash, {event.content_sha256 for event in terms})

    @override_settings(EMAIL_HOST='')
    def test_failed_otp_delivery_rolls_back_acceptance_events(self):
        response = self.client.post(reverse('register'), self.data())
        self.assertEqual(response.status_code, 200)
        self.assertFalse(User.objects.filter(email='sam@example.test').exists())
        self.assertEqual(LegalAcceptanceEvent.objects.count(), 0)


class PublicLegalPagesTests(TestCase):
    def test_production_operator_check_names_missing_values(self):
        with override_settings(DEBUG=False, CAREERGPS_OPERATOR={}):
            findings = public_operator_details(None)
        self.assertEqual(len(findings), 1)
        self.assertIsInstance(findings[0], Warning)
        self.assertIn('CAREERGPS_OPERATOR_NAME', findings[0].msg)

    def test_anonymous_pages_and_footer(self):
        for name in ('terms', 'privacy', 'about', 'contact', 'accessibility'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                for footer_name in ('terms', 'privacy', 'about', 'contact', 'accessibility'):
                    self.assertContains(response, reverse(footer_name))
                footer = response.content.decode().split('<footer class="public-footer">', 1)[1]
                self.assertNotIn('href="#"', footer)
        self.assertContains(self.client.get(reverse('register')), 'name="terms_agreed"')
        self.assertContains(self.client.get(reverse('login')), reverse('privacy'))
        self.assertNotContains(self.client.get(reverse('landing')), 'Verified Opportunities')
        self.assertNotContains(self.client.get(reverse('password_reset_done')),
                               'link will appear in the terminal')

    def test_signed_in_shells_offer_legal_links(self):
        user = User.objects.create_user('member', email='member@example.test',
                                        password='StrongCareerGPS2026!')
        Profile.objects.create(user=user)
        self.client.force_login(user)
        for name in ('dashboard', 'settings'):
            with self.subTest(page=name):
                response = self.client.get(reverse(name))
                self.assertEqual(response.status_code, 200)
                for footer_name in ('terms', 'privacy', 'contact'):
                    self.assertContains(response, reverse(footer_name))
        employer_home = self.client.get(reverse('employers:home'))
        self.assertEqual(employer_home.status_code, 200)
        for footer_name in ('terms', 'privacy', 'contact'):
            self.assertContains(employer_home, reverse(footer_name))

    def test_anonymous_cannot_post_privacy_request(self):
        response = self.client.post(reverse('contact'), {
            'category': 'access', 'description': 'Please send my data.',
        })
        self.assertRedirects(response, reverse('login'), fetch_redirect_response=False)
        self.assertEqual(PrivacyRequest.objects.count(), 0)


class PrivacyRequestTests(TestCase):
    def setUp(self):
        self.owner = User.objects.create_user('owner', email='owner@example.test', password='secret')
        self.other = User.objects.create_user('other', email='other@example.test', password='secret')
        self.client.force_login(self.owner)

    def test_request_history_is_private_and_staff_note_is_hidden(self):
        response = self.client.post(reverse('contact'), {
            'category': 'deletion', 'description': 'Please review account closure.',
        })
        item = PrivacyRequest.objects.get(requester=self.owner)
        self.assertRedirects(response, reverse('privacy_request_detail', args=[item.pk]),
                             fetch_redirect_response=False)
        self.assertTrue(User.objects.filter(pk=self.owner.pk).exists())
        item.staff_note = 'Internal-only decision note'
        item.save(update_fields=['staff_note'])
        detail = self.client.get(reverse('privacy_request_detail', args=[item.pk]))
        self.assertContains(detail, 'Please review account closure.')
        self.assertNotContains(detail, item.staff_note)
        self.assertIn('no-store', detail['Cache-Control'])
        self.client.force_login(self.other)
        self.assertEqual(self.client.get(reverse('privacy_request_detail', args=[item.pk])).status_code, 404)
        self.assertNotContains(self.client.get(reverse('contact')), 'Please review account closure.')

    def test_rate_limit_fifth_request_allowed_sixth_rejected(self):
        for number in range(5):
            self.client.post(reverse('contact'), {'category': 'other', 'description': f'Question {number}'})
        response = self.client.post(reverse('contact'), {'category': 'other', 'description': 'Question six'})
        self.assertEqual(response.status_code, 200)
        self.assertEqual(PrivacyRequest.objects.filter(requester=self.owner).count(), 5)
