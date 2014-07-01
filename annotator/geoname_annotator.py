#!/usr/bin/env python
"""Token Annotator"""

import re
from collections import defaultdict

import pymongo

from annotator import *
from ngram_annotator import NgramAnnotator
from geonames_api import get_wikipedia_title_or_fallback
from geopy.distance import vincenty

class GeonameAnnotator:

    def __init__(self, geonames_collection=None):
        if not geonames_collection:
            db = pymongo.Connection('localhost', port=27017)['geonames']
            geonames_collection = db.allCountries
        self.geonames_collection = geonames_collection

    # TODO text in this case means AnnoText, elswhere, it's raw text
    def annotate_doc(self, doc):

        if 'ngrams' not in doc.sentences[0].tiers:
            print "Annotating ngrams..."
            ngram_annotator = NgramAnnotator()
            ngram_annotator.annotate_doc(doc)

        all_ngrams = set([span.label
                          for sentence in doc.sentences
                          for span in sentence.tiers['ngrams'].spans])

        print "Running big query on {num} ngrams...".format(num=len(all_ngrams))
        geoname_cursor = self.geonames_collection.find({
            'name' : { '$in' : list(all_ngrams) }
        })
        geoname_results = list(geoname_cursor)
        print 'Big query done., got {num} results'.format(num=len(geoname_results))
        candidates_by_name = defaultdict(list)
        for location in geoname_results:
            candidates_by_name[location['name']].append(location)

        # iterative resolution
        resolved_locations_by_name = {}
        rejected_locations_by_name = {}
        delete_queue = []
        round_id = 0

        previous_length = 999

        while len(resolved_locations_by_name) != previous_length:
            round_id += 1
            print "Starting round:", round_id

            previous_length = len(resolved_locations_by_name)

            for location_name in delete_queue:
                del(candidates_by_name[location_name])
            delete_queue = []

            for location_name, candidates in candidates_by_name.iteritems():
                scored_candidates = [
                    (self.score_candidate(candidate, resolved_locations_by_name.values()), candidate)
                    for candidate in candidates
                    ]
                sorted_candidates = sorted(scored_candidates, reverse=True)
                if sorted_candidates[0][0] >= 20 or (len(scored_candidates) <= 2 and sorted_candidates[0][0] >= 5):
                    print "resolving ", candidate['name']
                    resolved_locations_by_name[location_name] = candidate
                    rejected_locations_by_name[location_name] = sorted_candidates[1:]
                    delete_queue.append(location_name)
                    next

        print "Resolved {0} locations out of {1}".format(
            len(resolved_locations_by_name), len(candidates_by_name))

        for sentence in doc.sentences:
            sentence.tiers['geonames'] = AnnoTier()
            for span in sentence.tiers['ngrams'].spans:
                if span.label in resolved_locations_by_name:
                    location = resolved_locations_by_name[span.label]
                    label = location['name']
                    geo_span = AnnoSpan(span.start, span.end, 
                                        span.sentence,
                                        label=label) 
                    sentence.tiers['geonames'].spans.append(geo_span)

        return doc

    def score_candidate(self, candidate, resolved_locations):
        if candidate['population'] > 1000000:
            population_score = 100
        elif candidate['population'] > 500000:
            population_score = 50
        elif candidate['population'] > 10000:
            population_score = 10
        else:
            population_score = 0

        close_locations = 0
        if resolved_locations:
            total_distance = 0.0
            for location in resolved_locations:
                distance = vincenty(
                    (candidate['latitude'], candidate['longitude']),
                    (location['latitude'], location['longitude'])
                    ).kilometers
                total_distance += distance
                if total_distance < 10:
                    close_locations += 100
                elif total_distance < 100:
                    close_locations += 50
                elif total_distance < 1000:
                    close_locations += 10

            average_distance = total_distance / len(resolved_locations)
            distance_score = average_distance / 100

        return population_score + close_locations



