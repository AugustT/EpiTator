#!/usr/bin/env python
import sys
import unittest
import test_utils
sys.path = ['./'] + sys.path

from annotator.annotator import AnnoDoc
from annotator.patient_info_annotator import PatientInfoAnnotator

class PatientInfoAnnotatorTest(unittest.TestCase):

    def setUp(self):
        self.annotator = PatientInfoAnnotator()

    def test_snippit1(self):
        # based on: http://www.promedmail.org/direct.php?id=2638359
        # Hyphenated words are a problem that has not been addressed yet.
        # Phrases like 10-year-old are treated as single words by pattern
        # which makes it so we can't search for them.
        # Numeric ranges separated by a hyphen (e.g. 1-14) also get treated
        # as an individual entity, and to make matters more complicated,
        # they can be confused with date strings and number like ninety-nine.
        doc = AnnoDoc("""
        A 10 year old girl of Sippi village in Upper Subansiri district,
        who was admitted to Arunachal State Hospital died on Thursday,
        state epidemiologist Dr L Jampa told PTI on Saturday [26 Jul 2014].
        The other affected people include children in the age group of
        approximately 1 - 14 years besides an 18 year old, he stated.
        They were undergoing treatment at the Arunachal State Hospital [ASH]
        since last week, while approximately 12 cases being treated in Guwahati
        Medical College Hospital in Assam were reportedly improving, he said.
        """)
        doc.add_tier(self.annotator)
        test_utils.assertHasProps(doc.tiers['patientInfo'].spans[0].metadata, {
            'age' : {
                'number': 10,
                'year_units': True,
            },
            'female': True
        })
        test_utils.assertHasProps(doc.tiers['patientInfo'].spans[1].metadata, {
            'age' : {
                'child': True,
            }
        })
        test_utils.assertHasProps(doc.tiers['patientInfo'].spans[2].metadata, {
            'age' : {
                'range_start': 1,
                'range_end': 14,
                'approximate': True,
                'year_units': True
            }
        })
        test_utils.assertHasProps(doc.tiers['patientInfo'].spans[3].metadata, {
            'age' : {
                'number': 18,
                'year_units': True
            }
        })

    def test_snippit2(self):
        # based on: http://www.promedmail.org/direct.php?id=2641341
        doc = AnnoDoc("""
        If the person has a positive history of previous infection with Q fever,
        vaccination is contraindicated; in others, serum antibody determination
        and skin tests must be done before vaccination to exclude those
        likely to have severe local or systemic hypersensitivity reactions to
        the vaccine resulting from previous unrecognized exposure to the organism
        (http://www.immunise.health.gov.au/internet/immunise/publishing.nsf/Content/handbook10-4-15).
        Q fever vaccine is not routinely recommended for children aged less than
        15 years of age and pregnant or breastfeeding women because safety of
        the vaccine in these situations has not been established.
        Revaccination is not indicated due to the possibility of severe
        hypersensitivity reactions.
        """)
        doc.add_tier(self.annotator)
        # This should probably be folded into the other match somehow
        test_utils.assertHasProps(doc.tiers['patientInfo'].spans[0].metadata, {
            'age' : {
                'child': True
            }
        })
        test_utils.assertHasProps(doc.tiers['patientInfo'].spans[1].metadata, {
            'age' : {
                'max': 15,
                'year_units': True
            }
        })
        test_utils.assertHasProps(doc.tiers['patientInfo'].spans[2].metadata, {
            'female' : True
        })

    def test_senior(self):
        # based on: http://www.promedmail.org/direct.php?id=2070771
        doc = AnnoDoc("""
        The senior physician of the Chelyabinsk Region organised immunisation of
        all medical workers, teachers, and school employees under the age of 35.
        According to officials, 58 cases of measles had been confirmed so far.
        """)
        doc.add_tier(self.annotator)
        test_utils.assertHasProps(doc.tiers['patientInfo'].spans[0].metadata, {
            'age' : {
                'max': 35
            }
        })

    def test_key_cat(self):
        # based on: http://www.promedmail.org/direct.php?id=2499795
        doc = AnnoDoc("""
        Details of the cases are as follows:
        - A 71 year old male farmer from Meizhou City, Guandong Province.
        He had onset of symptoms on [10 May 2014],
        was admitted to a hospital on [12 May 2014],
        and is currently in a mild condition.
        He had a history of exposure to live poultry.
        """)
        doc.add_tier(self.annotator, keyword_categories={
            'occupation' : [
                'farmer',
                'hospital worker'
            ]
        })
        test_utils.assertHasProps(doc.tiers['patientInfo'].spans[0].metadata, {
            'age' : {
                'number': 71,
                'year_units': True
            },
            'male': True,
            'occupation': 'farmer'
        })

    def test_count_discrimination(self):
        """
        Test that we can discriminate between count numbers and age numbers.
        """
        doc = AnnoDoc("""
        The other affected people include children in the age group of
        approximately 1 - 14 years besides an 18 year old, he stated.
        """)
        doc.add_tier(self.annotator)
        self.assertEqual(test_utils.get_path(
            doc.tiers['patientInfo'].spans[0].metadata,
            'count.range_start'
        ), None)

    def test_count_and_age(self):
        doc = AnnoDoc("""
        The other 12 cases include children in the age
        group of 4 - 14 years besides one elderly male approximately 60 years of
        age and one six month old infant.
        """)
        doc.add_tier(self.annotator)
        self.assertEqual(test_utils.get_path(
            doc.tiers['patientInfo'].spans[0].metadata,
            'count.range_start'
        ), None)

    def test_count_and_age2(self):
        # based on: http://www.promedmail.org/direct.php?id=2558448
        doc = AnnoDoc("""
        A 45 year old woman succumbed to rabies at the civic-run Naidu
        Infectious Diseases Hospital on Friday [20 Jun 2014], taking the fatal
        infection's death toll in the city to 15 this year [2014].
        """)
        doc.add_tier(self.annotator)
        test_utils.assertHasProps(doc.tiers['patientInfo'].spans[0].metadata, {
            'age' : {
                'number': 45,
                'year_units': True
            },
            'female': True
        })
        test_utils.assertHasProps(doc.tiers['patientInfo'].spans[1].metadata, {
            'count' : {
                'number': 15,
                'death': True
            }
        })

    def test_attribute_association(self):
        doc = AnnoDoc("""
        The first deaths reported were a seventy-two year old woman
        and a 6 month old infant.
        """)
        doc.add_tier(self.annotator)
        test_utils.assertHasProps(doc.tiers['patientInfo'].spans[0].metadata, {
            'age' : {
                'number': 72,
                'year_units': True
            },
            'female': True,
            'death' : True
        })
        test_utils.assertHasProps(doc.tiers['patientInfo'].spans[1].metadata, {
            'age' : {
                'number': 6,
                'month_units': True
            },
            'death' : True
        })
        self.assertEqual(test_utils.get_path(
            doc.tiers['patientInfo'].spans[1].metadata,
            'count.female'
        ), None)

    def test_hyphenated_age(self):
        doc = AnnoDoc("forty-six-year-old")
        doc.add_tier(self.annotator)
        test_utils.assertHasProps(doc.tiers['patientInfo'].spans[0].metadata, {
            'age' : {
                'number': 46,
                'year_units': True
            }
        })

    def test_speed(self):
        """
        Some articles can be very slow to process when using longest_total
        matches to do replacement.
        This test checks that the processing time is reasonable.
        """
        from datetime import datetime, timedelta
        start = datetime.utcnow()
        doc = AnnoDoc("""
        [3] UK (England) - HPA report Date: Fri 22 Aug 2014 Source: Health Protection Report, Volume 8, No 33 [edited] http://www.hpa.org.uk/hpr/infections/immunisation.htm Measles -- April-June 2014 -------------------------- 16 measles infections with onset dates in the April to June 2014 quarter were confirmed in England compared to 70 cases in the 1st quarter of the year [2014]. 7 of the confirmed infections were identified in London with 4 cases reported from West Midlands, 3 cases the South East and one case each from Eastern and East Midlands regions. Across the UK, Scotland reported one case linked to recent travel to Viet Nam but there were no measles cases reported from Wales or Northern Ireland. 7 of the 16 English cases in the period reported a history of recent travel; 5 cases to the Far East (China and Viet Nam) one each to Malawi and United Arab Emirates. Measles virus sequence was obtained from 11 out of the 16 English cases and the single Scottish case which either confirmed the importation of infection or suggested links to an importation. 7 cases this quarter were in children aged 1 to 4 years and the remaining 9 cases were adults aged 20 to 64 years. Only one case reported previously receiving a measles-containing vaccine. In the 12-month period July 2013 to June 2014, countries within the European Union and European Economic Area (EU/EEA) reported a total of 7116 cases. More than 3/4ths of the cases were reported from 3 countries; the Netherlands 34.4 percent, Italy 30.7 percent and Germany 12.2 percent. The previously reported outbreak in the Hague in the Netherlands is now over, but new outbreaks have been reported recently in Sweden and Belgium. Several outbreaks in EU Member States have a serological and epidemiological link to the large ongoing outbreak in the Philippines with 47 000 cases.
        """)
        doc.add_tier(self.annotator)
        self.assertTrue(
            (datetime.utcnow() - start) < timedelta(seconds=5)
        )

if __name__ == '__main__':
    unittest.main()