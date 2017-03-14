#!/usr/bin/env python
"""Geoname Annotator"""
import math
import re
import itertools
import sqlite3
from collections import defaultdict
from lazy import lazy

from annotator import *
from ngram_annotator import NgramAnnotator
from ne_annotator import NEAnnotator
from geopy.distance import great_circle
from maximum_weight_interval_set import Interval, find_maximum_weight_interval_set

from get_database_connection import get_database_connection
import math
import geoname_classifier

import datetime
import logging
logging.basicConfig(level=logging.ERROR, format='%(asctime)s %(message)s')
logger = logging.getLogger(__name__)

# TODO: We might be able to remove some of these names in a more general way
# by adding a feature to the scoring function.
blocklist = [
    'January', 'February', 'March', 'April', 'May', 'June', 'July',
    'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday', 'Sunday',
    'August', 'September', 'October', 'November', 'December',
    'North', 'East', 'West', 'South',
    'Northeast', 'Southeast', 'Northwest', 'Southwest',
    'Eastern', 'Western', 'Southern', 'Northern',
    'About', 'Many', 'See', 'As', 'About', 'Health',
    'International', 'City', 'World', 'Federal', 'Federal District',
    'British', 'Russian',
    'Valley', 'University', 'Center', 'Central',
    # These locations could be legitimate,
    # but they are rarely referred to in a context
    # where its location is relevent.
    'National Institutes of Health',
    'Centers for Disease Control',
    'Ministry of Health and Sanitation',
]

GEONAME_SCORE_THRESHOLD = 0.2

def location_contains(loc_outer, loc_inner):
    """
    Do a comparison to see if one geonames location contains another.
    It returns an integer to indicate the level of containment.
    0 indicates no containment. Siblings locations and identical locations
    have 0 containment. The level of containment is determined by the specificty
    of the outer location. e.g. USA would be a smaller number than Texas.
    In order for containment to be detected the outer location must have a
    ADM* or PCL* feature code, which is most countries, states, and districts.
    """
    props = [
        'country_code',
        'admin1_code',
        'admin2_code',
        'admin3_code',
        'admin4_code'
    ]
    if loc_outer['geonameid'] == loc_inner['geonameid']:
        return 0
    if re.match("^PCL.", loc_outer['feature_code']):
        outer_feature_level = 1
    elif loc_outer['feature_code'] == 'ADM1':
        outer_feature_level = 2
    elif loc_outer['feature_code'] == 'ADM2':
        outer_feature_level = 3
    elif loc_outer['feature_code'] == 'ADM3':
        outer_feature_level = 4
    elif loc_outer['feature_code'] == 'ADM4':
        outer_feature_level = 5
    else:
        return 0
    for prop in props[:outer_feature_level]:
        if loc_outer[prop] == '':
            return 0
        if loc_outer[prop] != loc_inner[prop]:
            return 0
    return outer_feature_level

class GeoSpan(AnnoSpan):
    def __init__(self, start, end, doc, geoname):
        self.start = start
        self.end = end
        self.doc = doc
        self.geoname = geoname
        self.label = geoname['name']
    def to_dict(self):
        result = super(GeoSpan, self).to_dict()
        result['geoname'] = self.geoname.to_dict()
        return result

class GeonameRow(sqlite3.Row):
    def __init__(self, *args):
        super(GeonameRow, self).__init__(*args)
        self.alternate_locations = set()
        self.spans = set()
        self.parent_location = None
        self.score = None
    def add_spans(self, span_text_to_spans):
        for name in self['names_used'].split(';'):
            for span in span_text_to_spans[name.lower().strip()]:
                self.spans.add(span)
    @lazy
    def lat_long(self):
        return (self['latitude'], self['longitude'])
    def __hash__(self):
        return id(self)
    def __repr__(self):
        return self['name']
    def to_dict(self):
        result = {}
        for key in self.keys():
            result[key] = self[key]
        if self.parent_location:
            result['parent_location'] = self.parent_location.to_dict()
        result['score'] = self.score
        return result

class GeonameFeatures(object):
    feature_names = [
        'log_population',
        'name_count',
        'num_spans',
        'max_span_length',
        'cannonical_name_used',
        'NEs_contained',
        'ambiguity',
        'PPL_feature_code',
        'ADM_feature_code_score',
        'CONT_feature_code',
        # contextual features
        'close_locations',
        'containing_locations',
        'max_containment_level',
        # This is inverted so a zero from undefined contextual features
        # doesn't boost the score.
        'inv_closest_location_distance_km',
        ]
    def __init__(self, geoname):
        self.geoname = geoname
        self.nearby_mentions = []
        d = {}
        d['log_population'] = math.log(geoname['population'] + 1)
        # Geonames with lots of alternate names
        # tend to be the ones most commonly referred to.
        d['name_count'] = geoname['name_count']
        d['num_spans'] = len(geoname.spans)
        d['max_span_length'] = max([
            len(span.text) for span in geoname.spans])
        d['cannonical_name_used'] = 1 if any([
            span.text == geoname['name'] for span in geoname.spans
        ]) else 0
        NE_overlap = 0
        total_len = 0
        for span in geoname.spans:
            ne_spans = span.doc.tiers['nes'].spans_in_span(span)
            total_len += len(span.text)
            for ne_span in ne_spans:
                if ne_span.label == 'GPE':
                    NE_overlap += len(ne_span.text)
        d['NEs_contained'] = float(NE_overlap) / total_len
        d['ambiguity'] = len(geoname.alternate_locations)
        feature_code = geoname['feature_code']
        d['PPL_feature_code'] = 1 if feature_code.startswith('PPL') else 0
        d['ADM_feature_code_score'] = 1 if feature_code.startswith('ADM') else 0
        d['CONT_feature_code'] = 1 if feature_code.startswith('CONT') else 0
        self._values = [0] * len(self.feature_names)
        for idx, name in enumerate(self.feature_names):
            if name in d:
                self._values[idx] = d[name]
    def set_value(self, feature_name, value):
        self._values[self.feature_names.index(feature_name)] = value
    def add_contextual_features(self):
        geoname = self.geoname
        close_locations = 0
        inv_closest_location_distance_km = 0
        containing_locations = 0
        max_containment_level = 0
        for feature in self.nearby_mentions:
            recently_mentioned_geoname = feature.geoname
            containment_level = max(
                location_contains(geoname, recently_mentioned_geoname),
                location_contains(recently_mentioned_geoname, geoname))
            if containment_level > 0:
                containing_locations += 1
            if containment_level > max_containment_level:
                max_containment_level = containment_level
            distance = great_circle(
                recently_mentioned_geoname.lat_long, geoname.lat_long
            ).kilometers
            if distance < 1.0:
                inv_distance = 1.0
            else:
                inv_distance = 1.0 / distance
            if inv_distance > inv_closest_location_distance_km:
                inv_closest_location_distance_km = inv_distance
            if distance < 500:
                close_locations += 1
        d = dict(
            close_locations=close_locations,
            containing_locations=containing_locations,
            max_containment_level=max_containment_level,
            inv_closest_location_distance_km=inv_closest_location_distance_km)
        for idx, name in enumerate(self.feature_names):
            if name in d:
                self._values[idx] = d[name]
    def to_dict(self):
        return {
            key: value
            for key, value in zip(self.feature_names, self._values)}
    def values(self):
        return self._values

class GeonameAnnotator(Annotator):
    def __init__(self):
        self.connection = get_database_connection()
        self.connection.row_factory = GeonameRow
    def get_candidate_geonames(self, doc):
        """
        Returns an array of geoname dicts correponding to locations that the document may refer to.
        The dicts are extended with lists of associated AnnoSpans.
        """
        if 'ngrams' not in doc.tiers:
            ngram_annotator = NgramAnnotator()
            doc.add_tier(ngram_annotator)
        if 'nes' not in doc.tiers:
            ne_annotator = NEAnnotator()
            doc.add_tier(ne_annotator)
        logger.info('Named entities annotated')
        all_ngrams = list(set([span.text.lower()
            for span in doc.tiers['ngrams'].spans
            if span.text not in blocklist and
            # We can rule out a few FPs by only looking at capitalized names.
            span.text[0] == span.text[0].upper()
        ]))
        logger.info('%s ngrams extracted' % len(all_ngrams))
        cursor = self.connection.cursor()
        results  = cursor.execute('''
        SELECT
            geonames.*,
            count AS name_count,
            group_concat(alternatename, ";") AS names_used
        FROM geonames
        JOIN alternatename_counts USING ( geonameid )
        JOIN alternatenames USING ( geonameid )
        WHERE alternatename_lemmatized IN (''' +
        ','.join('?' for x in all_ngrams) +
        ''') GROUP BY geonameid''', all_ngrams)
        geoname_results = list(results)#[Geoname(result) for result in results]
        logger.info('%s geonames fetched' % len(geoname_results))
        # Associate spans with the geonames.
        # This is done up front so span information can be used in the scoring
        # function
        span_text_to_spans = {
            span.text.lower() : []
            for span in doc.tiers['ngrams'].spans
        }
        for span in doc.tiers['ngrams'].spans:
            span_text_to_spans[span.text.lower()].append(span)
        candidate_locations = []
        for geoname in geoname_results:
            geoname.add_spans(span_text_to_spans)
            candidate_locations.append(geoname)
        # Add combined spans to locations that are adjacent to a span linked to
        # an administrative division. e.g. Seattle, WA
        span_to_locations = {}
        for location in candidate_locations:
            for span in location.spans:
                span_to_locations[span] =\
                    span_to_locations.get(span, []) + [location]
        for span_a, span_b in itertools.permutations(
            list(span_to_locations.keys()), 2
        ):
            if not span_a.comes_before(span_b, max_dist=4): continue
            if (
                len(
                    set(span_a.doc.text[span_a.end:span_b.start]) - set(", ")
                ) > 1
            ): continue
            combined_span = span_a.extended_through(span_b)
            possible_locations = []
            for loc_a, loc_b in itertools.product(
                span_to_locations[span_a],
                span_to_locations[span_b],
            ):
                if(
                    loc_b['feature_code'].startswith('ADM') and
                    loc_a['feature_code'] != loc_b['feature_code']
                ):
                    if location_contains(loc_b, loc_a) > 0:
                        loc_a.spans.add(combined_span)
                        loc_a.parent_location = loc_b
        # Find locations with overlapping spans
        for idx, location_a in enumerate(candidate_locations):
            a_spans = location_a.spans
            for location_b in candidate_locations[idx + 1:]:
                b_spans = location_b.spans
                if len(a_spans & b_spans) > 0:
                    # Note that is is possible for two valid locations to have
                    # overlapping names. For example, Harare Province has
                    # Harare as an alternate name, so the city Harare is very
                    # to be an alternate location that competes with it.
                    location_a.alternate_locations.add(location_b)
                    location_b.alternate_locations.add(location_a)
        logger.info('%s candidate locations prepared' % len(candidate_locations))
        return candidate_locations
    def extract_features(self, locations):
        return [GeonameFeatures(location) for location in locations]
    def add_contextual_features(self, features):
        """
        Set additional feature values that are based on the geonames mentioned
        nearby.
        """
        span_to_features = defaultdict(list)
        for feature in features:
            for span in feature.geoname.spans:
                span_to_features[span].append(feature)
        geoname_span_tier = AnnoTier(span_to_features.keys())
        geoname_span_tier.sort_spans()

        def feature_generator():
            for span in geoname_span_tier.spans:
                for feature in span_to_features[span]:
                    yield span.start, feature
        feature_gen = feature_generator()
        resolved_feature_gen = feature_generator()

        # A ring buffer containing the recently mentioned resolved geoname features.
        rf_buffer = []
        rf_buffer_idx = 0
        BUFFER_SIZE = 10

        # The number of characters to lookahead searching for nearby mentions.
        LOOKAHEAD_OFFSET = 50
        rf_gen_end = False
        rf_start = 0
        f_start = 0

        # Fill the buffer to capacity with initially mentioned resolved features.
        while len(rf_buffer) < BUFFER_SIZE:
            try:
                rf_start, feature = next(resolved_feature_gen)
                if feature.geoname.high_confidence:
                    rf_buffer.append(feature)
            except StopIteration:
                rf_gen_end = True
                break
        while True:
            while rf_gen_end or f_start < rf_start - LOOKAHEAD_OFFSET:
                try:
                    f_start, feature = next(feature_gen)
                except StopIteration:
                    for feature in features:
                        feature.add_contextual_features()
                    return
                feature.nearby_mentions += rf_buffer
            while True:
                try:
                    rf_start, maybe_resolved_feature = next(resolved_feature_gen)
                except StopIteration:
                    rf_gen_end = True
                    break
                if maybe_resolved_feature.geoname.high_confidence:
                    rf_buffer[rf_buffer_idx % BUFFER_SIZE] = maybe_resolved_feature
                    rf_buffer_idx += 1
                    break
    def cull_geospans(self, geo_spans):
        mwis = find_maximum_weight_interval_set([
            Interval(
                geo_span.start,
                geo_span.end,
                # If the size is equal the score is used as a tie breaker.
                geo_span.size() + geo_span.geoname.score,
                geo_span
            )
            for geo_span in geo_spans
        ])
        retained_spans = [interval.corresponding_object for interval in mwis]
        logger.info('overlapping geospans removed')
        return retained_spans
    def annotate(self, doc):
        logger.info('geoannotator started')
        candidate_geonames = self.get_candidate_geonames(doc)
        features = self.extract_features(candidate_geonames)
        scores = geoname_classifier.predict_proba_base([f.values() for f in features])
        for location, score in zip(candidate_geonames, scores):
            location.high_confidence = float(score[1]) > geoname_classifier.HIGH_CONFIDENCE_THRESHOLD

        self.add_contextual_features(features)
        
        scores = geoname_classifier.predict_proba_contextual([f.values() for f in features])
        for location, score in zip(candidate_geonames, scores):
            location.score = float(score[1])
        
        culled_locations = [location
            for location in candidate_geonames
            if location.score > GEONAME_SCORE_THRESHOLD]
        geo_spans = []
        for location in culled_locations:
            for span in location.spans:
                geo_span = GeoSpan(
                    span.start, span.end, doc, location)
                geo_spans.append(geo_span)
        culled_geospans = self.cull_geospans(geo_spans)
        doc.tiers['geonames'] = AnnoTier(culled_geospans)
        return doc
