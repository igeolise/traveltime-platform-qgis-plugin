import json
import os
import math
import random
from qgis.PyQt.QtCore import QVariant
from qgis.PyQt.QtGui import QColor

from qgis.core import (
    QgsFeatureSink,
    QgsCoordinateTransform,
    QgsProcessing,
    QgsProcessingParameterBoolean,
    QgsProcessingParameterFeatureSource,
    QgsProcessingParameterFeatureSink,
    QgsProcessingParameterExpression,
    QgsProcessingParameterEnum,
    QgsProcessingParameterNumber,
    QgsProcessingParameterField,
    QgsWkbTypes,
    QgsPoint,
    QgsLineString,
    QgsFields,
    QgsField,
    QgsFeature,
    QgsGeometry,
    QgsExpression,
    QgsExpressionContext,
    QgsFeatureRequest,
    QgsProcessingUtils,
    QgsCategorizedSymbolRenderer,
    QgsLineSymbol,
    QgsRendererCategory,
    NULL,
)

from .. import resources
from .. import utils

from ..utils import tr

from .base import AlgorithmBase, EPSG4326

# Constants to define behaviour of available properties
PROPERTY_DEFAULT_NO = 0
PROPERTY_DEFAULT_YES = 1
PROPERTY_ALWAYS = 2


class _SearchAlgorithmBase(AlgorithmBase):
    """Base class for the algorithms that share properties such as departure/arrival_searches"""

    available_properties = {}

    def initAlgorithm(self, config):
        """Base setup of the algorithm.

        This will setup parameters corresponding to departure_searches and arrival_searches,
        since they are common to all main algorithms.

        Subclasses should call this and then define their own parameters.

        Note that there are slight differences on the API side on the departure_searches and
        arrival_searches parameters: for the time-map endpoint, the coords are included in these
        parameters, while for the time-filter and routes endpoints, the coords are all defined
        in a list of locations.

        Here we will implement everything as it is for the time-map endpoint, as it maps better
        to normal GIS workflow, where you usually have the list to filter and the inputs points
        as different datasets. The mapping to the different API data model will be done in the
        processing algorithm by the time-filter and routes subclasses.
        """

        for DEPARR in ["DEPARTURE", "ARRIVAL"]:
            self.addParameter(
                QgsProcessingParameterFeatureSource(
                    "INPUT_" + DEPARR + "_SEARCHES",
                    "{} / Searches".format(DEPARR.title()),
                    [QgsProcessing.TypeVectorPoint],
                    optional=True,
                ),
                help_text=tr(
                    "Searches based on departure time. Leave departure location at no earlier than given time. You can define a maximum of 10 searches"
                ),
            )
            self.addParameter(
                QgsProcessingParameterExpression(
                    "INPUT_" + DEPARR + "_ID",
                    "{} / ID".format(DEPARR.title()),
                    optional=True,
                    defaultValue="'" + DEPARR.lower() + "_searches_' || $id",
                    parentLayerParameterName="INPUT_" + DEPARR + "_SEARCHES",
                ),
                help_text=tr(
                    "Used to identify this specific search in the results array. MUST be unique among all searches."
                ),
            )
            # Transportation
            self.addParameter(
                QgsProcessingParameterExpression(
                    "INPUT_" + DEPARR + "_TRNSPT_TYPE",
                    "{} / Transportation / type".format(DEPARR.title()),
                    optional=True,
                    defaultValue="'public_transport'",
                    parentLayerParameterName="INPUT_" + DEPARR + "_SEARCHES",
                ),
                help_text=tr(
                    "cycling, driving, driving+train (only in Great Britain), public_transport, walking, coach, bus, train, ferry, driving+ferry, cycling+ferry or cycling+public_transport (only in Netherlands)"
                ),
            )
            self.addParameter(
                QgsProcessingParameterExpression(
                    "INPUT_" + DEPARR + "_TRNSPT_PT_CHANGE_DELAY",
                    "{} / Transportation / change delay".format(DEPARR.title()),
                    optional=True,
                    defaultValue="0",
                    parentLayerParameterName="INPUT_" + DEPARR + "_SEARCHES",
                ),
                advanced=True,
                help_text=tr(
                    "Time (in seconds) needed to board public transportation vehicle. Default is 0. Cannot be higher than travel_time. Used in public_transport, coach, bus, train, driving+train and cycling+public_transport transportation modes"
                ),
            )
            self.addParameter(
                QgsProcessingParameterExpression(
                    "INPUT_" + DEPARR + "_TRNSPT_WALKING_TIME",
                    "{} / Transportation / walking time".format(DEPARR.title()),
                    optional=True,
                    defaultValue="900",
                    parentLayerParameterName="INPUT_" + DEPARR + "_SEARCHES",
                ),
                advanced=True,
                help_text=tr(
                    "Maximum time (in seconds) of walking from source to a station/stop and from station/stop to destination. Default value is 900. Cannot be higher than travel_time. Used in public_transport, coach, bus, train, driving+train and cycling+public_transport transportation modes"
                ),
            )
            self.addParameter(
                QgsProcessingParameterExpression(
                    "INPUT_" + DEPARR + "_TRNSPT_DRIVING_TIME_TO_STATION",
                    "{} / Transportation / driving time to station".format(
                        DEPARR.title()
                    ),
                    optional=True,
                    defaultValue="1800",
                    parentLayerParameterName="INPUT_" + DEPARR + "_SEARCHES",
                ),
                advanced=True,
                help_text=tr(
                    "Maximum time (in seconds) of driving from source to train station. Default value is 1800. Cannot be higher than travel_time. Used in driving+train transportation mode"
                ),
            )
            self.addParameter(
                QgsProcessingParameterExpression(
                    "INPUT_" + DEPARR + "_TRNSPT_CYCLING_TIME_TO_STATION",
                    "{} / Transportation / cycling time to station".format(
                        DEPARR.title()
                    ),
                    optional=True,
                    defaultValue="900",
                    parentLayerParameterName="INPUT_" + DEPARR + "_SEARCHES",
                ),
                advanced=True,
                help_text=tr(
                    "Maximum time (in seconds) of cycling (including any ferry transfers) from source to a station or stop. Default value is 900. Cannot be higher than travel_time. Used in cycling+public_transport transportation mode"
                ),
            )
            self.addParameter(
                QgsProcessingParameterExpression(
                    "INPUT_" + DEPARR + "_TRNSPT_PARKING_TIME",
                    "{} / Transportation / parking time".format(DEPARR.title()),
                    optional=True,
                    defaultValue="300",
                    parentLayerParameterName="INPUT_" + DEPARR + "_SEARCHES",
                ),
                advanced=True,
                help_text=tr(
                    "Time (in seconds) required to park a car or a bike. Default is 300. Cannot be higher than travel_time. Used in driving+train and cycling+public_transport transportation modes."
                ),
            )
            self.addParameter(
                QgsProcessingParameterExpression(
                    "INPUT_" + DEPARR + "_TRNSPT_BOARDING_TIME",
                    "{} / Transportation / boarding time".format(DEPARR.title()),
                    optional=True,
                    defaultValue="0",
                    parentLayerParameterName="INPUT_" + DEPARR + "_SEARCHES",
                ),
                advanced=True,
                help_text=tr(
                    "Time (in seconds) required to board a ferry. Default is 0. Cannot be higher than travel_time. Used in public_transport, ferry, driving+ferry, cycling+ferry and cycling+public_transport transportation modes. For public_transport mode, pt_change_delay is used instead"
                ),
            )
            self.addParameter(
                QgsProcessingParameterExpression(
                    "INPUT_" + DEPARR + "_RANGE_WIDTH",
                    "{} / Search range width ".format(DEPARR.title()),
                    optional=True,
                    defaultValue="null",
                    parentLayerParameterName="INPUT_" + DEPARR + "_SEARCHES",
                ),
                advanced=True,
                help_text=tr(
                    "Search range width in seconds. width along with departure_time specify departure interval. For example, if you set departure_time to 9am and width to 1 hour, we will return a combined shape of all possible journeys that have departure time between 9am and 10am. Range width is limited to 12 hours"
                ),
            )
            self.addParameter(
                QgsProcessingParameterExpression(
                    "INPUT_" + DEPARR + "_TIME",
                    "{} / Time".format(DEPARR.title()),
                    optional=True,
                    defaultValue="'{}'".format(utils.now_iso()),
                    parentLayerParameterName="INPUT_" + DEPARR + "_SEARCHES",
                ),
                help_text=tr(
                    "Leave departure location at no earlier than given time. Example - 2017-10-18T08:00:00Z"
                ),
            )
            self.addParameter(
                QgsProcessingParameterExpression(
                    "INPUT_" + DEPARR + "_TRAVEL_TIME",
                    "{} / Travel time".format(DEPARR.title()),
                    optional=True,
                    defaultValue="900",
                    parentLayerParameterName="INPUT_" + DEPARR + "_SEARCHES",
                ),
                help_text=tr(
                    "Travel time in seconds. Maximum value is 14400 (4 hours)"
                ),
            )
        self.addParameter(
            QgsProcessingParameterNumber(
                "INPUT_SEARCH_RANGE_MAX_RESULTS",
                "Max results when search range enabled",
                defaultValue=1,
                minValue=1,
                maxValue=5,
            ),
            advanced=True,
            help_text=tr(
                "Maximum number of results to return if a range is specified. Max is 5 results."
            ),
        )

        for prop, behaviour in self.available_properties.items():
            if behaviour == PROPERTY_ALWAYS:
                continue
            self.addParameter(
                QgsProcessingParameterBoolean(
                    "PROPERTIES_" + prop.upper(),
                    "Properties / {}".format(prop),
                    optional=True,
                    defaultValue=(behaviour == PROPERTY_DEFAULT_YES),
                ),
                advanced=True,
                help_text=tr(
                    "Retrieve the property '{}'. Make sure this property is available in the region of your searches."
                ).format(prop),
            )

        # Define output parameters
        self.addParameter(
            QgsProcessingParameterFeatureSink(
                "OUTPUT", tr("Output"), type=self.output_type
            )
        )

    def processAlgorithmGetSlices(self, parameters, context, feedback):
        """Gets the slices to subdivide queries in smaller chunks"""
        slices = list(self._processAlgorithmYieldSlices(parameters, context, feedback))

        if len(slices) > 1:
            feedback.pushInfo(
                tr(
                    "Due to the large amount of features, the request will be chunked in {} API calls. This may have unexpected consequences on some parameters. Keep an eye on your API usage !"
                ).format(len(slices))
            )

        return slices

    def _processAlgorithmYieldSlices(self, parameters, context, feedback):
        """Yields slices to subdivide queries in smaller chunks"""

        departure_count = (
            self.params["INPUT_DEPARTURE_SEARCHES"].featureCount()
            if self.params["INPUT_DEPARTURE_SEARCHES"]
            else 0
        )
        arrival_count = (
            self.params["INPUT_ARRIVAL_SEARCHES"].featureCount()
            if self.params["INPUT_ARRIVAL_SEARCHES"]
            else 0
        )

        slicing_size = 10
        slicing_count = math.ceil(max(departure_count, arrival_count) / slicing_size)

        for i in range(slicing_count):
            yield {
                "search_slice_start": i * slicing_size,
                "search_slice_end": (i + 1) * slicing_size,
            }

    def processAlgorithmRemixData(self, data, parameters, context, feedback):
        """To be overriden by subclasses : allow to edit the data object before sending to the API"""
        return data

    def processAlgorithmPrepareSearchData(
        self, slicing_start, slicing_end, parameters, context, feedback
    ):
        """This method prepares the data array with all parameters corresponding to the common search attributes

        The slicing_start/end params allow to prepare just a slice, to conform to API limitation (for now 10 searches/query)
        """
        data = {}
        for DEPARR in ["DEPARTURE", "ARRIVAL"]:
            source = self.params["INPUT_" + DEPARR + "_SEARCHES"]
            deparr = DEPARR.lower()
            if source:
                feedback.pushDebugInfo("Loading {} searches features...".format(deparr))
                data[deparr + "_searches"] = []
                xform = QgsCoordinateTransform(
                    source.sourceCrs(), EPSG4326, context.transformContext()
                )
                for i, feature in enumerate(source.getFeatures()):
                    # Stop the algorithm if cancel button has been clicked
                    # if feedback.isCanceled():
                    #     break

                    if i < slicing_start or i >= slicing_end:
                        continue

                    # Set feature for expression context
                    self.expressions_context.setFeature(feature)

                    # Reproject to WGS84
                    geometry = feature.geometry()
                    geometry.transform(xform)

                    search_data = {
                        "id": self.eval_expr("INPUT_" + DEPARR + "_ID"),
                        "coords": {
                            "lat": geometry.asPoint().y(),
                            "lng": geometry.asPoint().x(),
                        },
                        "transportation": {
                            "type": self.eval_expr("INPUT_" + DEPARR + "_TRNSPT_TYPE"),
                            "pt_change_delay": self.eval_expr(
                                "INPUT_" + DEPARR + "_TRNSPT_PT_CHANGE_DELAY"
                            ),
                            "walking_time": self.eval_expr(
                                "INPUT_" + DEPARR + "_TRNSPT_WALKING_TIME"
                            ),
                            "driving_time_to_station": self.eval_expr(
                                "INPUT_" + DEPARR + "_TRNSPT_DRIVING_TIME_TO_STATION"
                            ),
                            "cycling_time_to_station": self.eval_expr(
                                "INPUT_" + DEPARR + "_TRNSPT_CYCLING_TIME_TO_STATION"
                            ),
                            "parking_time": self.eval_expr(
                                "INPUT_" + DEPARR + "_TRNSPT_PARKING_TIME"
                            ),
                            "boarding_time": self.eval_expr(
                                "INPUT_" + DEPARR + "_TRNSPT_BOARDING_TIME"
                            ),
                        },
                        deparr + "_time": self.eval_expr("INPUT_" + DEPARR + "_TIME"),
                        "travel_time": self.eval_expr(
                            "INPUT_" + DEPARR + "_TRAVEL_TIME"
                        ),
                        "properties": self.enabled_properties(),
                    }
                    range_width = self.eval_expr("INPUT_" + DEPARR + "_RANGE_WIDTH")
                    if range_width:
                        range_data_dict = {"enabled": True, "width": range_width}
                        if self.has_param("INPUT_SEARCH_RANGE_MAX_RESULTS"):
                            range_data_dict["max_results"] = self.params[
                                "INPUT_SEARCH_RANGE_MAX_RESULTS"
                            ]
                        search_data.update({"range": range_data_dict})

                    data[deparr + "_searches"].append(search_data)

                    # # Update the progress bar
                    # feedback.setProgress(int(current * total))
        return data

    def enabled_properties(self):
        """Returns the list of properties that are enabled"""
        return [
            prop
            for prop, behaviour in self.available_properties.items()
            if behaviour == PROPERTY_ALWAYS or self.params["PROPERTIES_" + prop.upper()]
        ]


class TimeMapAlgorithm(_SearchAlgorithmBase):
    url = "/v4/time-map"
    accept_header = "application/vnd.wkt+json"
    available_properties = {"is_only_walking": PROPERTY_DEFAULT_YES}
    output_type = QgsProcessing.TypeVectorPolygon

    _name = "time_map"
    _displayName = "Time Map"
    _group = "Advanced"
    _groupId = "advanced"
    _icon = resources.icon_time_map_advanced
    _helpUrl = "http://docs.traveltimeplatform.com/reference/time-map/"
    _shortHelpString = tr(
        "This algorithms allows to use the time-map endpoint from the TravelTime platform API.\n\nIt matches the endpoint data structure as closely as possible. Please see the help on {url} for more details on how to use it.\n\nConsider using the simplified algorithms as they may be easier to work with."
    ).format(url=_helpUrl)

    RESULT_TYPE = ["NORMAL", "UNION", "INTERSECTION"]

    def initAlgorithm(self, config):

        # Define all common DEPARTURE and ARRIVAL parameters
        super().initAlgorithm(config)

        for DEPARR in ["DEPARTURE", "ARRIVAL"]:
            self.addParameter(
                QgsProcessingParameterField(
                    "INPUT_" + DEPARR +"_EXISTING_FIELDS_TO_KEEP",
                    "{} / [fields to keep]".format(DEPARR.title()),
                    optional=True,
                    allowMultiple=True,
                    parentLayerParameterName="INPUT_" + DEPARR + "_SEARCHES",
                ),
                advanced=True,
                help_text=tr("Set which fields should be joined back in the output layer."),
            )

        # Define additional input parameters
        self.addParameter(
            QgsProcessingParameterEnum(
                "OUTPUT_RESULT_TYPE",
                tr("Result aggregation"),
                options=self.RESULT_TYPE,
                defaultValue=0,
            ),
            help_text=tr(
                "NORMAL will return a polygon for each departure/arrival search. UNION will return the union of all polygons for all departure/arrivals searches. INTERSECTION will return the intersection of all departure/arrival searches."
            ),
        )

        self.removeParameter("INPUT_SEARCH_RANGE_MAX_RESULTS")

    def doProcessAlgorithm(self, parameters, context, feedback):

        # Configure common expressions inputs
        self.processAlgorithmConfigureParams(parameters, context, feedback)

        # Slice queries if needed
        slices = self.processAlgorithmGetSlices(parameters, context, feedback)

        # Make the query (in slices)
        results = []
        for slice_ in slices:

            slc_start = slice_["search_slice_start"]
            slc_end = slice_["search_slice_end"]

            # Prepare the data
            data = self.processAlgorithmPrepareSearchData(
                slc_start, slc_end, parameters, context, feedback
            )

            # Remix the data as needed
            data = self.processAlgorithmRemixData(data, parameters, context, feedback)

            # Make the query
            response_data = self.processAlgorithmMakeRequest(
                parameters, context, feedback, data=data
            )

            results += response_data["results"]

        feedback.pushDebugInfo("Loading response to layer...")

        # Configure output
        return self.processAlgorithmOutput(results, parameters, context, feedback)

    def processAlgorithmOutput(self, results, parameters, context, feedback):
        output_fields = QgsFields()

        output_fields.append(QgsField("id", QVariant.String, "text"))

        for prop in self.enabled_properties():
            output_fields.append(QgsField("prop_" + prop, QVariant.String, "text"))

        for deparr in ["departure", "arrival"]:
            DEPARR = deparr.upper()
            input_layer = self.params["INPUT_" + DEPARR + "_SEARCHES"]
            if not input_layer:
                continue
            for field_name in self.params["INPUT_" + DEPARR +"_EXISTING_FIELDS_TO_KEEP"]:
                old_field = input_layer.fields().field(field_name)
                new_field = QgsField(old_field)
                new_field.setName("original_" + deparr + "_" + old_field.name())
                output_fields.append(new_field)

        (sink, sink_id) = self.parameterAsSink(
            parameters,
            "OUTPUT",
            context,
            output_fields,
            QgsWkbTypes.MultiPolygon,
            EPSG4326,
        )

        result_type = self.RESULT_TYPE[self.params["OUTPUT_RESULT_TYPE"]]

        aggregate_geom = None
        for result in results:
            if result_type == "NORMAL":
                feature = QgsFeature(output_fields)
                feature.setAttribute("id", result["search_id"])
                for prop in self.enabled_properties():
                    feature.setAttribute(
                        "prop_" + prop, result["properties"].get(prop, NULL)
                    )
                feature.setGeometry(QgsGeometry.fromWkt(result["shape"]))

                # dirty section where we join back columns from the input layer
                for deparr in ["departure", "arrival"]:
                    DEPARR = deparr.upper()
                    input_layer = self.params["INPUT_" + DEPARR + "_SEARCHES"]
                    if input_layer and self.params["INPUT_" + DEPARR +"_EXISTING_FIELDS_TO_KEEP"]:

                        expr = QgsExpression(
                            "{expr} = '{id}'".format(
                                expr=self.params["INPUT_" + DEPARR + "_ID"].expression(), id=result["search_id"],
                            )
                        )

                        # this should return an iterator with only one feature
                        existing_features = input_layer.getFeatures(
                            QgsFeatureRequest(expr)
                        )
                        try:
                            existing_feature = existing_features.__next__()
                            for field_name in self.params["INPUT_" + DEPARR +"_EXISTING_FIELDS_TO_KEEP"]:
                                feature.setAttribute(
                                    "original_" + deparr + "_" + field_name,
                                    existing_feature.attribute(field_name),
                                )
                            break
                        except StopIteration:
                            feedback.reportError(
                                "Couldn't find source feature for result {} (using following expression : {}).".format(
                                    result["search_id"], expr.expression()
                                )
                            )

                # Add a feature in the sink
                sink.addFeature(feature, QgsFeatureSink.FastInsert)
            else:
                # Build the aggregated feature
                geom = QgsGeometry.fromWkt(result["shape"])

                if aggregate_geom is None:
                    aggregate_geom = geom
                else:
                    if result_type == "UNION":
                        aggregate_geom = aggregate_geom.combine(geom)
                    elif result_type == "INTERSECTION":
                        aggregate_geom = aggregate_geom.intersection(geom)
                    else:
                        raise Exception("Unsupported aggregation operator")

        if aggregate_geom.wkbType() == QgsWkbTypes.GeometryCollection:
            aggregate_geom.convertGeometryCollectionToSubclass(QgsWkbTypes.MultiPolygon)

        if result_type != "NORMAL":
            feature = QgsFeature(output_fields)
            feature.setAttribute("id", result_type)
            feature.setGeometry(aggregate_geom)
            sink.addFeature(feature, QgsFeatureSink.FastInsert)

        feedback.pushDebugInfo("TimeMapAlgorithm done !")

        # to get hold of the layer in post processing
        self.sink_id = sink_id

        return {"OUTPUT": sink_id}

    def postProcessAlgorithm(self, context, feedback):

        result_type = self.RESULT_TYPE[self.params["OUTPUT_RESULT_TYPE"]]

        if result_type == "NORMAL":
            style_file = "style_time.qml"
        elif result_type == "UNION":
            style_file = "style_time_union.qml"
        elif result_type == "INTERSECTION":
            style_file = "style_time_intersection.qml"

        style_path = os.path.join(os.path.dirname(__file__), "styles", style_file)
        QgsProcessingUtils.mapLayerFromString(self.sink_id, context).loadNamedStyle(
            style_path
        )

        return super().postProcessAlgorithm(context, feedback)


class TimeFilterAlgorithm(_SearchAlgorithmBase):
    url = "/v4/time-filter"
    accept_header = "application/json"
    available_properties = {
        "travel_time": PROPERTY_DEFAULT_YES,
        "distance": PROPERTY_DEFAULT_YES,
        "distance_breakdown": PROPERTY_DEFAULT_NO,
        "fares": PROPERTY_DEFAULT_NO,
        "route": PROPERTY_DEFAULT_NO,
    }
    output_type = QgsProcessing.TypeVectorPoint

    _name = "time_filter"
    _displayName = "Time Filter"
    _group = "Advanced"
    _groupId = "advanced"
    _icon = resources.icon_time_filter_advanced
    _helpUrl = "http://docs.traveltimeplatform.com/reference/time-filter/"
    _shortHelpString = tr(
        "This algorithms allows to use the time-filter endpoint from the TravelTime platform API.\n\nIt matches the endpoint data structure as closely as possible. The key difference with the API is that the filter is automatically done on ALL locations, while the API technically allows to specify which locations to filter for each search.\n\nPlease see the help on {url} for more details on how to use it.\n\nConsider using the simplified algorithms as they may be easier to work with."
    ).format(url=_helpUrl)

    def initAlgorithm(self, config):

        # Define all common DEPARTURE and ARRIVAL parameters
        super().initAlgorithm(config)

        # Define additional input parameters
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                "INPUT_LOCATIONS",
                tr("Locations"),
                [QgsProcessing.TypeVectorPoint],
                optional=False,
            ),
            help_text=tr(
                "The list of locations to filter. In contrast to the API, this algorithm filters ALL locations, while the API allows to specify which arrival_location_ids/departure_location_ids to filter."
            ),
        )
        self.addParameter(
            QgsProcessingParameterExpression(
                "INPUT_LOCATIONS_ID",
                "Locations ID",
                optional=True,
                defaultValue="'locations_' || $id",
                parentLayerParameterName="INPUT_LOCATIONS",
            ),
            help_text=tr(
                "You will have to reference this id in your searches. It will also be used in the response body. MUST be unique among all locations."
            ),
        )

    def doProcessAlgorithm(self, parameters, context, feedback):

        # Configure common expressions inputs
        self.processAlgorithmConfigureParams(parameters, context, feedback)

        # Slice queries if needed
        slices = self.processAlgorithmGetSlices(parameters, context, feedback)

        # Make the query (in slices)
        results = []
        for slice_ in slices:

            slc_start = slice_["search_slice_start"]
            slc_end = slice_["search_slice_end"]

            # Prepare the data
            data = self.processAlgorithmPrepareSearchData(
                slc_start, slc_end, parameters, context, feedback
            )

            # Remix the data as needed
            data = self.processAlgorithmRemixData(
                data, slice_, parameters, context, feedback
            )

            # Make the query
            response_data = self.processAlgorithmMakeRequest(
                parameters, context, feedback, data=data
            )

            results += response_data["results"]

        feedback.pushDebugInfo("Loading response to layer...")

        # Configure output
        return self.processAlgorithmOutput(results, parameters, context, feedback)

    def processAlgorithmRemixData(self, data, slice_, parameters, context, feedback):
        locations = self.params["INPUT_LOCATIONS"]

        # Prepare location data (this is the same for all the slices)
        data["locations"] = []
        xform = QgsCoordinateTransform(
            locations.sourceCrs(), EPSG4326, context.transformContext()
        )

        slc_start = slice_["loc_slice_start"]
        slc_end = slice_["loc_slice_end"]

        for i, feature in enumerate(locations.getFeatures()):

            if i < slc_start or i >= slc_end:
                continue

            # Set feature for expression context
            self.expressions_context.setFeature(feature)
            geometry = feature.geometry()
            geometry.transform(xform)
            data["locations"].append(
                {
                    "id": self.eval_expr("INPUT_LOCATIONS_ID"),
                    "coords": {
                        "lat": geometry.asPoint().y(),
                        "lng": geometry.asPoint().x(),
                    },
                }
            )

        # Currently, the API requires all geoms to be passed in the locations parameter
        # and refers to them using departure_location_id and arrival_location_ids in the
        # departure_searches definition.
        # Here we remix the data array to conform to this data model.
        all_locations_ids = [l["id"] for l in data["locations"]]
        if "departure_searches" in data:
            for departure_search in data["departure_searches"]:
                data["locations"].append(
                    {"id": departure_search["id"], "coords": departure_search["coords"]}
                )
                del departure_search["coords"]
                departure_search["departure_location_id"] = departure_search["id"]
                departure_search["arrival_location_ids"] = all_locations_ids
        if "arrival_searches" in data:
            for arrival_search in data["arrival_searches"]:
                data["locations"].append(
                    {"id": arrival_search["id"], "coords": arrival_search["coords"]}
                )
                del arrival_search["coords"]
                arrival_search["arrival_location_id"] = arrival_search["id"]
                arrival_search["departure_location_ids"] = all_locations_ids

        return data

    def processAlgorithmOutput(self, results, parameters, context, feedback):
        locations = self.params["INPUT_LOCATIONS"]

        output_fields = QgsFields(locations.fields())
        output_fields.append(QgsField("search_id", QVariant.String, "text"))
        output_fields.append(QgsField("reachable", QVariant.Int, "int"))

        for prop in self.enabled_properties():
            output_fields.append(QgsField("prop_" + prop, QVariant.String, "text"))

        output_crs = locations.sourceCrs()
        output_type = locations.wkbType()

        (sink, sink_id) = self.parameterAsSink(
            parameters, "OUTPUT", context, output_fields, output_type, output_crs
        )

        def clone_feature(id_):
            """Returns a feature cloned from the locations layer"""
            id_expr = self.parameterAsString(parameters, "INPUT_LOCATIONS_ID", context)
            expression_ctx = self.createExpressionContext(parameters, context)
            expression = QgsExpression("{} = '{}'".format(id_expr, id_))
            return utils.clone_feature(
                QgsFeatureRequest(expression, expression_ctx), locations, output_fields
            )

        for result in results:

            for location in result["locations"]:
                for properties in location["properties"]:
                    feature = clone_feature(location["id"])
                    feature.setAttribute("search_id", result["search_id"])
                    feature.setAttribute("reachable", 1)
                    for prop in self.enabled_properties():
                        feature.setAttribute(
                            "prop_" + prop, json.dumps(properties[prop])
                        )
                    sink.addFeature(feature, QgsFeatureSink.FastInsert)
            for id_ in result["unreachable"]:
                feature = clone_feature(id_)
                feature.setAttribute("search_id", result["search_id"])
                feature.setAttribute("reachable", 0)
                sink.addFeature(feature, QgsFeatureSink.FastInsert)

        feedback.pushDebugInfo("TimeFilterAlgorithm done !")

        # to get hold of the layer in post processing
        self.sink_id = sink_id

        return {"OUTPUT": sink_id}

    def postProcessAlgorithm(self, context, feedback):
        style_file = "style_filter.qml"
        style_path = os.path.join(os.path.dirname(__file__), "styles", style_file)
        QgsProcessingUtils.mapLayerFromString(self.sink_id, context).loadNamedStyle(
            style_path
        )
        return super().postProcessAlgorithm(context, feedback)

    def _processAlgorithmYieldSlices(self, parameters, context, feedback):
        """Yields slices to subdivide queries in smaller chunks"""

        locations_count = self.params["INPUT_LOCATIONS"].featureCount()

        slicing_size = 2000
        slicing_count = math.ceil(locations_count / slicing_size)

        for i in range(slicing_count):
            for slice_ in super()._processAlgorithmYieldSlices(
                parameters, context, feedback
            ):
                slice_.update(
                    {
                        "loc_slice_start": i * slicing_size,
                        "loc_slice_end": (i + 1) * slicing_size,
                    }
                )
                yield slice_


class RoutesAlgorithm(_SearchAlgorithmBase):
    url = "/v4/routes"
    accept_header = "application/json"
    available_properties = {
        "travel_time": PROPERTY_DEFAULT_YES,
        "distance": PROPERTY_DEFAULT_YES,
        "fares": PROPERTY_DEFAULT_NO,
        "route": PROPERTY_ALWAYS,
    }
    output_type = QgsProcessing.TypeVectorLine

    _name = "routes"
    _displayName = "Routes"
    _group = "Advanced"
    _groupId = "advanced"
    _icon = resources.icon_routes_advanced
    _helpUrl = "http://docs.traveltimeplatform.com/reference/routes/"
    _shortHelpString = tr(
        "This algorithms allows to use the routes endpoint from the TravelTime platform API.\n\nIt matches the endpoint data structure as closely as possible. The key difference with the API is that the routes are automatically computd on ALL locations, while the API technically allows to specify which locations to filter for each search.\n\nPlease see the help on {url} for more details on how to use it.\n\nConsider using the simplified algorithms as they may be easier to work with."
    ).format(url=_helpUrl)

    RESULT_TYPE = ["BY_ROUTE", "BY_DURATION", "BY_TYPE"]

    def initAlgorithm(self, config):

        # Define all common DEPARTURE and ARRIVAL parameters
        super().initAlgorithm(config)

        # Remove unused parameters
        self.removeParameter("INPUT_DEPARTURE_TRAVEL_TIME")
        self.removeParameter("INPUT_ARRIVAL_TRAVEL_TIME")

        # Define additional input parameters
        self.addParameter(
            QgsProcessingParameterFeatureSource(
                "INPUT_LOCATIONS",
                tr("Locations"),
                [QgsProcessing.TypeVectorPoint],
                optional=False,
            ),
            help_text=tr(
                "Define your locations to use later in departure_searches or arrival_searches"
            ),
        )
        self.addParameter(
            QgsProcessingParameterExpression(
                "INPUT_LOCATIONS_ID",
                "Locations ID",
                optional=True,
                defaultValue="'locations_' || $id",
                parentLayerParameterName="INPUT_LOCATIONS",
            ),
            help_text=tr(
                "You will have to reference this id in your searches. It will also be used in the response body. MUST be unique among all locations."
            ),
        )

        # Define output parameters
        self.addParameter(
            QgsProcessingParameterEnum(
                "OUTPUT_RESULT_TYPE",
                tr("Output style"),
                options=self.RESULT_TYPE,
                defaultValue=0,
            ),
            help_text=tr(
                "BY_ROUTE and BY_DURATION will return a simple linestring for each route. BY_TYPE will return several segments for each type of transportation for each route."
            ),
        )

    def doProcessAlgorithm(self, parameters, context, feedback):

        # Configure common expressions inputs
        self.processAlgorithmConfigureParams(parameters, context, feedback)

        # Slice queries if needed
        slices = self.processAlgorithmGetSlices(parameters, context, feedback)

        # Make the query (in slices)
        results = []
        for slice_ in slices:

            slc_start = slice_["search_slice_start"]
            slc_end = slice_["search_slice_end"]

            # Prepare the data
            data = self.processAlgorithmPrepareSearchData(
                slc_start, slc_end, parameters, context, feedback
            )

            # Remix the data as needed
            data = self.processAlgorithmRemixData(
                data, slice_, parameters, context, feedback
            )

            # Make the query
            response_data = self.processAlgorithmMakeRequest(
                parameters, context, feedback, data=data
            )

            results += response_data["results"]

        feedback.pushDebugInfo("Loading response to layer...")

        # Configure output
        return self.processAlgorithmOutput(results, parameters, context, feedback)

    def processAlgorithmRemixData(self, data, slice_, parameters, context, feedback):
        locations = self.params["INPUT_LOCATIONS"]

        # Prepare location data
        data["locations"] = []
        xform = QgsCoordinateTransform(
            locations.sourceCrs(), EPSG4326, context.transformContext()
        )

        slc_start = slice_["loc_slice_start"]
        slc_end = slice_["loc_slice_end"]

        for i, feature in enumerate(locations.getFeatures()):

            if i < slc_start or i >= slc_end:
                continue

            # Set feature for expression context
            self.expressions_context.setFeature(feature)
            geometry = feature.geometry()
            geometry.transform(xform)
            data["locations"].append(
                {
                    "id": self.eval_expr("INPUT_LOCATIONS_ID"),
                    "coords": {
                        "lat": geometry.asPoint().y(),
                        "lng": geometry.asPoint().x(),
                    },
                }
            )

        # Currently, the API requires all geoms to be passed in the locations parameter
        # and refers to them using departure_location_id and arrival_location_ids in the
        # departure_searches definition.
        # Here we remix the data array to conform to this data model.
        all_locations_ids = [l["id"] for l in data["locations"]]
        if "departure_searches" in data:
            for departure_search in data["departure_searches"]:
                data["locations"].append(
                    {"id": departure_search["id"], "coords": departure_search["coords"]}
                )
                del departure_search["coords"]
                del departure_search["travel_time"]
                departure_search["departure_location_id"] = departure_search["id"]
                departure_search["arrival_location_ids"] = all_locations_ids
        if "arrival_searches" in data:
            for arrival_search in data["arrival_searches"]:
                data["locations"].append(
                    {"id": arrival_search["id"], "coords": arrival_search["coords"]}
                )
                del arrival_search["coords"]
                del arrival_search["travel_time"]
                arrival_search["arrival_location_id"] = arrival_search["id"]
                arrival_search["departure_location_ids"] = all_locations_ids

        return data

    def processAlgorithmOutput(self, results, parameters, context, feedback):
        output_fields = QgsFields()
        result_type = self.RESULT_TYPE[self.params["OUTPUT_RESULT_TYPE"]]
        output_fields.append(QgsField("search_id", QVariant.String, "text"))
        output_fields.append(QgsField("location_id", QVariant.String, "text"))

        if result_type == "BY_ROUTE" or result_type == "BY_DURATION":
            for prop in self.enabled_properties():
                output_fields.append(
                    QgsField("prop_" + prop, QVariant.String, "text")
                )
        else:
            output_fields.append(QgsField("part_id", QVariant.Int, "int"))
            output_fields.append(QgsField("part_type", QVariant.String, "text"))
            output_fields.append(QgsField("part_mode", QVariant.String, "text"))
            output_fields.append(
                QgsField("part_directions", QVariant.String, "text")
            )
            output_fields.append(QgsField("part_distance", QVariant.Int, "int"))
            output_fields.append(QgsField("part_travel_time", QVariant.Int, "int"))

        output_crs = EPSG4326
        output_type = QgsWkbTypes.LineString

        (sink, sink_id) = self.parameterAsSink(
            parameters, "OUTPUT", context, output_fields, output_type, output_crs
        )

        for result in results:
            for location in result["locations"]:
                for properties in location["properties"]:

                    if result_type == "BY_ROUTE" or result_type == "BY_DURATION":

                        # Create the geom
                        geom = QgsLineString()
                        for part in properties["route"]["parts"]:
                            for coord in part["coords"]:
                                point = QgsPoint(coord["lng"], coord["lat"])
                                if geom.endPoint() != point:
                                    geom.addVertex(point)

                        # Create the feature
                        feature = QgsFeature(output_fields)
                        feature.setGeometry(geom)
                        feature.setAttribute("search_id", result["search_id"])
                        feature.setAttribute("location_id", location["id"])

                        for prop in self.enabled_properties():
                            feature.setAttribute(
                                "prop_" + prop, json.dumps(properties[prop])
                            )

                        sink.addFeature(feature, QgsFeatureSink.FastInsert)
                    else:
                        for part in properties["route"]["parts"]:

                            # Create the geom
                            geom = QgsLineString()
                            for coord in part["coords"]:
                                point = QgsPoint(coord["lng"], coord["lat"])
                                geom.addVertex(point)

                            # Create the feature
                            feature_d = QgsFeature(output_fields)
                            feature_d.setGeometry(geom)

                            feature_d.setAttribute("search_id", result["search_id"])
                            feature_d.setAttribute("location_id", location["id"])

                            feature_d.setAttribute("part_id", part["id"])
                            feature_d.setAttribute("part_type", part["type"])
                            feature_d.setAttribute("part_mode", part["mode"])
                            feature_d.setAttribute(
                                "part_directions", part["directions"]
                            )
                            feature_d.setAttribute("part_distance", part["distance"])
                            feature_d.setAttribute(
                                "part_travel_time", part["travel_time"]
                            )

                            sink.addFeature(feature_d, QgsFeatureSink.FastInsert)

        feedback.pushDebugInfo("TimeFilterAlgorithm done !")

        # to get hold of the layer in post processing
        self.sink_id = sink_id

        return {"OUTPUT": sink_id}

    def postProcessAlgorithm(self, context, feedback):
        layer = QgsProcessingUtils.mapLayerFromString(self.sink_id, context)
        result_type = self.RESULT_TYPE[self.params["OUTPUT_RESULT_TYPE"]]

        feedback.pushInfo("result type is : " + result_type)

        if result_type == "BY_ROUTE":
            exp = "'from ' || search_id || ' to ' || location_id"
            # We get all uniques routes
            expression = QgsExpression(exp)
            exp_ctx = QgsExpressionContext()

            values = set()
            for f in layer.getFeatures():
                exp_ctx.setFeature(f)
                values.add(expression.evaluate(exp_ctx))

            categories = []
            for value in sorted(values):
                symbol = QgsLineSymbol()
                symbol.setWidth(1)
                symbol.setColor(QColor.fromHsl(random.randint(0, 359), 255, 127))
                category = QgsRendererCategory(value, symbol, value)
                categories.append(category)

            renderer = QgsCategorizedSymbolRenderer(exp, categories)
            layer.setRenderer(renderer)
        else:
            if result_type == "BY_DURATION":
                style_file = "style_route_duration.qml"
            else:
                style_file = "style_route_mode.qml"
            style_path = os.path.join(os.path.dirname(__file__), "styles", style_file)
            layer.loadNamedStyle(style_path)
        return super().postProcessAlgorithm(context, feedback)

    def _processAlgorithmYieldSlices(self, parameters, context, feedback):
        """Yields slices to subdivide queries in smaller chunks"""

        locations_count = self.params["INPUT_LOCATIONS"].featureCount()

        slicing_size = 2
        slicing_count = math.ceil(locations_count / slicing_size)

        for i in range(slicing_count):
            for slice_ in super()._processAlgorithmYieldSlices(
                parameters, context, feedback
            ):
                slice_.update(
                    {
                        "loc_slice_start": i * slicing_size,
                        "loc_slice_end": (i + 1) * slicing_size,
                    }
                )
                yield slice_
