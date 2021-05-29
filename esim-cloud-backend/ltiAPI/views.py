from django.conf import settings
from django.contrib import messages
from django.views import View
from rest_framework import status
from rest_framework.response import Response
from rest_framework.views import APIView
from django.http import HttpResponseRedirect
from django.shortcuts import render
from pylti.common import LTIException, verify_request_common, \
    post_message, generate_request_xml
from drf_yasg.utils import swagger_auto_schema
from saveAPI.models import StateSave
from .models import lticonsumer
from .utils import consumers, get_reverse, message_identifier, \
    lis_result_sourcedid, oauth_consumer_key, lis_outcome_service_url
from .serializers import consumerSerializer, consumerResponseSerializer


def denied(r):
    return render(r, 'ltiAPI/denied.html')


class LTIExist(APIView):

    def get(self, request, save_id):
        try:
            consumer = lticonsumer.objects.get(save_id=save_id)
        except lticonsumer.DoesNotExist:
            return Response(data={"error": "LTIConsumer Not found"},
                            status=status.HTTP_404_NOT_FOUND)
        host = request.get_host()
        config_url = "http://" + host + "/api/lti/" + str(save_id) + '/config.xml/'
        response_data = {
            "consumer_key": consumer.consumer_key,
            "secret_key": consumer.secret_key,
            "config_url": config_url,
            "score": consumer.score
        }
        response_serializer = consumerResponseSerializer(data=response_data)
        if response_serializer.is_valid():
            return Response(response_serializer.data,
                            status=status.HTTP_200_OK)
        else:
            return Response(response_serializer.errors,
                            status=status.HTTP_400_BAD_REQUEST)


class LTIBuildApp(APIView):

    @swagger_auto_schema(request_body=consumerSerializer, 
                         responses={201: consumerResponseSerializer})
    def post(self, request):
        serialized = consumerSerializer(data=request.data)
        if serialized.is_valid():
            serialized.save()
            save_id = str(serialized.data["save_id"])
            host = request.get_host()
            config_url = "http://" + host + "/api/lti/" + save_id + '/config.xml/'
            response_data = {
                "consumer_key": serialized.data.get('consumer_key'),
                "secret_key": serialized.data.get('secret_key'),
                "config_url": config_url,
                "score": serialized.data.get('score')
            }
            print("Recieved POST for LTI APP:", response_data)
            response_serializer = consumerResponseSerializer(data=response_data)
            if response_serializer.is_valid():
                return Response(response_serializer.data,
                                status=status.HTTP_201_CREATED)
            else:
                return Response(response_serializer.errors,
                                status=status.HTTP_400_BAD_REQUEST)
        else:
            return Response(serialized.errors,
                            status=status.HTTP_400_BAD_REQUEST)


class LTIDeleteApp(APIView):

    def delete(self, request, save_id):
        queryset = lticonsumer.objects.all()
        try:
            consumer = queryset.get(save_id=save_id)
            consumer.delete()
            return Response(data={"Message": "Successfully deleted!"},
                            status=status.HTTP_204_NO_CONTENT)
        except lticonsumer.DoesNotExist:
            return Response(status=status.HTTP_404_NOT_FOUND)


class LTIConfigView(View):
    def get(self, request, save_id):
        try:
            saved_state = StateSave.objects.get(save_id=save_id)
        except StateSave.DoesNotExist:
            return render(request, 'ltiAPI/denied.html')

        if saved_state.shared:
            pass
        else:
            saved_state.shared = True
            saved_state.save()
        domain = self.request.get_host()
        launch_url = '%s://%s/%s' % (
            self.request.scheme, domain,
            settings.LTI_TOOL_CONFIGURATION.get('launch_url'))
        ctx = {
            'domain': domain,
            'launch_url': launch_url,
            'title': saved_state.name + ' and ' + str(saved_state.save_id),
            'description': str(saved_state.description),
            'course_navigation': settings.LTI_TOOL_CONFIGURATION.get('course_navigation'),
        }
        return render(request, 'ltiAPI/config.xml', context=ctx,
                      content_type='text/xml; charset=utf-8')


class LTIAuthView(APIView):
    """POST handler for the LTI login POST back call"""
    def post(self, request):
        # Extracts the LTI payload information
        params = {key: request.data[key] for key in request.data}
        # Maps the settings defined for the LTI consumer
        consumers_dict = consumers()
        # Builds the tool URL from the request
        url = request.build_absolute_uri()
        # Extracts the request headers from the request
        headers = request.META
        # Define the redirect url
        host = request.get_host()
        print("Got POST for validating LTI consumer")
        try:
            i = lticonsumer.objects.get(consumer_key=request.data.get('oauth_consumer_key'))
        except lticonsumer.DoesNotExist:
            print("Consumer does not exist on backend")
            return HttpResponseRedirect(get_reverse('ltiAPI:denied'))
        next_url = "http://" + host + "/eda/#editor?id=" + str(i.save_id.save_id)
        try:
            # Validate the incoming LTI
            verify_request_common(consumers_dict, url, request.method, headers, params)
            print("Verified consumer")
            grade = LTIPostGrade(params, request)
            # if grade:
            #     # If there is a return URL from the configured call the redirect URL
            #     # is updated with the one that is returned. This is to enable redirecting to
            #     # constructed URLs
            #     return HttpResponseRedirect(next_url)
            return HttpResponseRedirect(next_url)
        except LTIException:
            return HttpResponseRedirect(get_reverse('ltiAPI:denied'))


def LTIPostGrade(params, request):
    """
    Post grade to LTI consumer using XML
    :param: score: 0 <= score <= 1. (Score MUST be between 0 and 1)
    :return: True if post successful and score valid
    :exception: LTIPostMessageException if call failed
    """
    try:
        consumer = lticonsumer.objects.get(consumer_key=oauth_consumer_key(request))
        score = consumer.score
        print("Set score for grading")
    except ValueError:
        score = 0

    xml = generate_request_xml(
        message_identifier(), 'replaceResult',
        lis_result_sourcedid(request), score)

    post = post_message(
        consumers(), oauth_consumer_key(request),
        lis_outcome_service_url(request), xml)
    if not post:
        msg = ('An error occurred while saving your score. '
               'Please try again.')
        messages.add_message(request, messages.ERROR, msg)

        return False
        # raise LTIPostMessageException('Post grade failed')
    else:
        msg = 'Your score was submitted. Great job!'
        messages.add_message(request, messages.INFO, msg)

        return True
