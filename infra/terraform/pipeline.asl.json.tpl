{
  "Comment": "Transform one batch date in Glue, then check data quality.",
  "StartAt": "Transform",
  "States": {
    "Transform": {
      "Type": "Task",
      "Resource": "arn:aws:states:::glue:startJobRun.sync",
      "Parameters": {
        "JobName": "${glue_job_name}",
        "Arguments": {
          "--dt.$": "$.dt",
          "--run_id.$": "$$.Execution.Name"
        }
      },
      "ResultPath": "$.glue",
      "Retry": [
        {
          "Comment": "The Glue job runs one at a time; wait for the running job instead of failing.",
          "ErrorEquals": ["Glue.ConcurrentRunsExceededException"],
          "IntervalSeconds": 60,
          "BackoffRate": 1.2,
          "MaxAttempts": 20
        },
        {
          "Comment": "One retry is safe because the transform is idempotent.",
          "ErrorEquals": ["States.TaskFailed"],
          "IntervalSeconds": 60,
          "BackoffRate": 2,
          "MaxAttempts": 1
        }
      ],
      "Catch": [
        {
          "ErrorEquals": ["States.ALL"],
          "ResultPath": "$.error",
          "Next": "NotifyFailure"
        }
      ],
      "Next": "QualityGate"
    },
    "QualityGate": {
      "Type": "Task",
      "Resource": "arn:aws:states:::lambda:invoke",
      "Parameters": {
        "FunctionName": "${gate_lambda_arn}",
        "Payload": {
          "dt.$": "$.dt",
          "run_id.$": "$$.Execution.Name"
        }
      },
      "ResultSelector": {
        "gate.$": "$.Payload"
      },
      "ResultPath": "$.quality",
      "Retry": [
        {
          "ErrorEquals": [
            "Lambda.ServiceException",
            "Lambda.AWSLambdaException",
            "Lambda.SdkClientException",
            "Lambda.TooManyRequestsException"
          ],
          "IntervalSeconds": 2,
          "BackoffRate": 2,
          "MaxAttempts": 3
        }
      ],
      "Catch": [
        {
          "ErrorEquals": ["States.ALL"],
          "ResultPath": "$.error",
          "Next": "NotifyFailure"
        }
      ],
      "Next": "QualityChoice"
    },
    "QualityChoice": {
      "Type": "Choice",
      "Choices": [
        {
          "Variable": "$.quality.gate.passed",
          "BooleanEquals": true,
          "Next": "Done"
        }
      ],
      "Default": "NotifyDegraded"
    },
    "NotifyDegraded": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sns:publish",
      "Parameters": {
        "TopicArn": "${alert_topic_arn}",
        "Subject": "finpipe: data loaded, quality threshold exceeded",
        "Message.$": "States.JsonToString($.quality.gate)"
      },
      "ResultPath": null,
      "Next": "Done"
    },
    "NotifyFailure": {
      "Type": "Task",
      "Resource": "arn:aws:states:::sns:publish",
      "Parameters": {
        "TopicArn": "${alert_topic_arn}",
        "Subject": "finpipe: pipeline run FAILED",
        "Message.$": "States.JsonToString($)"
      },
      "ResultPath": null,
      "Next": "Failed"
    },
    "Done": {
      "Type": "Succeed"
    },
    "Failed": {
      "Type": "Fail",
      "Error": "PipelineFailed",
      "Cause": "See the alert email, the Glue job run and audit/latest/ for details."
    }
  }
}
