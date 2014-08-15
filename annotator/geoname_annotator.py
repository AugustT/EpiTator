#!/usr/bin/env python
"""Token Annotator"""
import math
import re
import itertools
import pymongo

from annotator import *
from ngram_annotator import NgramAnnotator
from ne_annotator import NEAnnotator
from geopy.distance import great_circle

def geoname_matches_original_ngram(geoname, original_ngrams):
    if (geoname['name'] in original_ngrams):
        return True
    else:
        for original_ngram in original_ngrams:
            if original_ngram in geoname['alternatenames']:
                return True

    return False

blocklist = ['January', 'February', 'March', 'April', 'May', 'June', 'July',
             'August', 'September', 'October', 'November', 'December',
             'International', 'North', 'East', 'West', 'South',
             'Eastern', 'Western', 'Southern', 'Northern',
             'About', 'Many', 'See', 'As', 'About', 'Center', 'Central',
             'City', 'World', 'University', 'Valley',
             # NIH could be legitimate, but rarely is it referred to in a context
             # where its location is relevent.
             'National Institutes of Health']


class GeonameAnnotator(Annotator):

    def __init__(self, geonames_collection=None):
        if not geonames_collection:
            db = pymongo.Connection('localhost', port=27017)['geonames']
            geonames_collection = db.allCountries
        self.geonames_collection = geonames_collection

    # TODO text in this case means AnnoText, elswhere, it's raw text
    def annotate(self, doc):

        if 'ngrams' not in doc.tiers:
            ngram_annotator = NgramAnnotator()
            doc.add_tier(ngram_annotator)
            ne_annotator = NEAnnotator()
            doc.add_tier(ne_annotator)

        all_ngrams = set([span.text
            for span in doc.tiers['ngrams'].spans
            if span.text not in blocklist and
            # We can rule out a few FPs by only looking at capitalized names.
            span.text[0] == span.text[0].upper()
        ])

        geoname_cursor = self.geonames_collection.find({
            '$or' : [
                { 'name' : { '$in' : list(all_ngrams) } },
                # I suspect using multiple indecies slows this
                # query down by a factor of two. It might be worthwhile
                # to add name to alternate names so we can just
                # search on that property.
                { 'alternatenames' : { '$in' : list(all_ngrams) } }
            ]
        })
        geoname_results = list(geoname_cursor)

        # Associate spans with the geonames.
        # This is done up front so span information can be used in the scoring
        # function
        span_text_to_spans = {
            span.text : []
            for span in doc.tiers['ngrams'].spans
        }
        for span in doc.tiers['ngrams'].spans:
            span_text_to_spans[span.text].append(span)
        class Location(dict):
            """
            This main purpose of this class is to create hashable dictionaries
            that we can use in sets.
            """
            def __hash__(self):
                return id(self)

        candidate_locations = []
        for location_dict in geoname_results:
            location = Location(location_dict)
            location['spans'] = set()
            location['alternateLocations'] = set()
            candidate_locations.append(location)
            geoname_results
            names = set([location['name']] + location['alternatenames'])
            for name in names:
                if name not in span_text_to_spans: continue
                for span in span_text_to_spans[name]:
                    location['spans'].add(span)
                    
        # Add combined spans to locations that are adjacent to a span linked to
        # an administrative division. e.g. Seattle, WA
        span_to_locations = {}
        for location in candidate_locations:
            for span in location['spans']:
                span_to_locations[span] =\
                    span_to_locations.get(span, []) + [location]

        for span_a, span_b in itertools.permutations(
            span_to_locations.keys(), 2
        ):
            if span_a.comes_before(span_b, max_dist=3):
                combined_span = span_a.extended_through(span_b)
                possible_locations = []
                for loc_a, loc_b in itertools.product(
                    span_to_locations[span_a],
                    span_to_locations[span_b],
                ):
                    # print 'loc:', loc_a['name'], loc_b['name'], loc_b['feature code']
                    # TODO? Check admin codes for containment
                    if(
                        loc_b['feature code'].startswith('ADM') and
                        loc_a['feature code'] != loc_b['feature code']
                    ):
                        loc_a['spans'].add(combined_span)
        
        # Find locations with overlapping spans
        for idx, location_a in enumerate(candidate_locations):
            a_spans = location_a['spans']
            for idx, location_b in enumerate(candidate_locations[idx + 1:]):
                b_spans = location_b['spans']
                if len(a_spans & b_spans) > 0:
                    # Note that is is possible for two valid locations to have
                    # overlapping names. For example, Harare Province has
                    # Harare as an alternate name, so the city Harare is very
                    # to be an alternate location that competes with it.
                    location_a['alternateLocations'].add(location_b)
                    location_b['alternateLocations'].add(location_a)
        # Iterative resolution
        # Add location with scores above the threshold to the resolved location.
        # Keep rescoring the remaining locations until no more can be resolved.
        remaining_locations = list(candidate_locations)
        resolved_locations = []
        THRESH = 60
        while True:
            for candidate in remaining_locations:
                candidate['score'] = self.score_candidate(
                    candidate, resolved_locations
                )
                # This is just for debugging, put FP and FN ids here to see
                # their score.
                if candidate['geonameid']  in ['888825']:
                    print (
                        candidate['name'],
                        candidate['spans'][0].text,
                        candidate['score']
                    )
            # If there are alternate locations with higher scores
            # give this candidate a zero.
            for candidate in remaining_locations:
                for alt in candidate['alternateLocations']:
                    # We end up with multiple locations for per span if they
                    # are resolved in different iterations or
                    # if the scores are exactly the same.
                    # TODO: This needs to be delt with in the next stage.
                    if candidate['score'] < alt['score']:
                        candidate['score'] = 0
                        break

            newly_resolved_candidates = [
                candidate
                for candidate in remaining_locations
                if candidate['score'] > THRESH
            ]
            resolved_locations.extend(newly_resolved_candidates)
            for candiate in newly_resolved_candidates:
                if candidate in remaining_locations:
                    remaining_locations.remove(candiate)
            if len(newly_resolved_candidates) == 0:
                break

        geo_spans = []
        for location in resolved_locations:
            # Copy the dict so we don't need to return a custom class.
            location = dict(location)
            for span in location['spans']:
                # Maybe we should try to rule out some of the spans that
                # might not actually be associated with the location.
                geo_span = AnnoSpan(
                    span.start, span.end, doc, label=location['name']
                )
                geo_span.geoname = location
                geo_spans.append(geo_span)
            # These properties are removed because they
            # cannot be easily jsonified.
            location.pop('alternateLocations')
            location.pop('spans')

        retained_spans = []
        for geo_span_a in geo_spans:
            retain_a_overlap = True
            for geo_span_b in geo_spans:
                if geo_span_a == geo_span_b: continue
                if geo_span_a.overlaps(geo_span_b):
                    if geo_span_b.size() > geo_span_a.size():
                        # geo_span_a is probably a component of geospan b,
                        # e.g. Washington in University of Washington
                        # We use the longer span because it's usually correct.
                        retain_a_overlap = False
                        break
                    elif geo_span_b.size() == geo_span_a.size():
                        # Ambiguous name, use the scores to decide.
                        if geo_span_a.geoname['score'] < geo_span_b.geoname['score']:
                            retain_a_overlap = False
                            break
            if not retain_a_overlap:
                continue
            retained_spans.append(geo_span_a)
        
        doc.tiers['geonames'] = AnnoTier(retained_spans)

        return doc

    def score_candidate(self, candidate, resolved_locations):
        """
        Return a score between 0 and 100
        """
        def population_score():
            if candidate['population'] > 1000000:
                return 100
            elif candidate['population'] > 500000:
                return 50
            elif candidate['population'] > 100000:
                return 10
            elif candidate['population'] > 10000:
                return 5
            else:
                return 0

        def synonymity():
            # Geonames with lots of alternate names
            # tend to be the ones most commonly referred to.
            # For examle, coutries have lots of alternate names.
            if len(candidate['alternatenames']) > 8:
                return 100
            elif len(candidate['alternatenames']) > 4:
                return 50
            elif len(candidate['alternatenames']) > 0:
                return 10
            else:
                return 0

        def span_score():
            return min(100, len(candidate['spans']))

        def short_span_score():
            return min(100, 10 * len([
                span for span in candidate['spans']
                if len(span.text) < 4
            ]))

        def cannonical_name_used():
            return 100 if any([
                span.text == candidate['name'] for span in candidate['spans']
            ]) else 0

        def overlapping_NEs():
            score = 0
            for span in candidate['spans']:
                ne_spans = span.doc.tiers['nes'].spans_at_span(span)
                for ne_span in ne_spans:
                    if ne_span.label == 'GPE':
                        score += 30
            return min(100, score)

        def distinctiveness():
            return 100 / (len(candidate['alternateLocations']) + 1)
        
        def max_span():
            return len(max([span.text for span in candidate['spans']]))

        def close_locations():
            score = 0
            if resolved_locations:
                total_distance = 0.0
                for location in resolved_locations:
                    distance = great_circle(
                        (candidate['latitude'], candidate['longitude']),
                        (location['latitude'], location['longitude'])
                        ).kilometers
                    total_distance += distance
                    if distance < 10:
                        score += 100
                    elif distance < 20:
                        score += 50
                    elif distance < 30:
                        score += 20
                    elif distance < 50:
                        score += 10
                    elif distance < 500:
                        score += 5
                    elif distance < 1000:
                        score += 2
                average_distance = total_distance / len(resolved_locations)
                distance_score = average_distance / 100
            return score

        if candidate['population'] < 1000 and candidate['feature class'] in ['A', 'P']:
            return 0

        if any([
            alt in resolved_locations
            for alt in candidate['alternateLocations']
        ]):
            return 0

        # Commented out features will not be evaluated.
        feature_weights = {
            population_score : 1.5,
            synonymity : 1.5,
            span_score : 0.2,
            short_span_score : (-4),
            overlapping_NEs : 1,
            distinctiveness : 1,
            max_span : 1,
            close_locations : 1,
            cannonical_name_used : 0.5,
        }
        return sum([
            score_fun() * float(weight)
            for score_fun, weight in feature_weights.items()
        ]) / math.sqrt(sum([x**2 for x in feature_weights.values()]))
